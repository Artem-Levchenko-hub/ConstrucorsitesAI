"""Node preload for pg8.22 actor context. Signing keys never enter the product process.

This bridge is compatibility plumbing, not an authorization boundary: PostgreSQL
validates signed tokens and enforces grants/RLS independently. Streaming/native
drivers are not silently treated as supported.
"""

NODE_BRIDGE_SOURCE = r"""'use strict';
const { AsyncLocalStorage } = require('node:async_hooks');
const Module = require('node:module');
const actor = new AsyncLocalStorage();
const clients = new WeakMap();
const patchedClients = new WeakSet();
const patchedPools = new WeakSet();
const patchedServers = new WeakSet();

function context() { return actor.getStore() || { token: '' }; }
function server(Server) {
  if (patchedServers.has(Server)) return;
  patchedServers.add(Server);
  const emit = Server.prototype.emit;
  Server.prototype.emit = function(event, ...args) {
    if (event !== 'request') return emit.call(this, event, ...args);
    const raw = args[0]?.headers?.['x-omnia-data-token'];
    const token = typeof raw === 'string' && raw.length <= 4096
      && /^[0-9a-f]+\.[0-9a-f]{64}$/.test(raw) ? raw : '';
    return actor.run({ token }, () => emit.call(this, event, ...args));
  };
}
server(require('node:http').Server);
server(require('node:https').Server);

function patchClient(Client) {
  if (!Client?.prototype?.query || patchedClients.has(Client)) return;
  patchedClients.add(Client);
  const original = Client.prototype.query;

  // pg error callbacks may precede ReadyForQuery. Never run the next actor until
  // the protocol reports the transaction's final status (including aborted tx).
  async function invoke(client, args) {
    let finish;
    const ready = new Promise(resolve => { finish = resolve; });
    const connection = client.connection;
    const cleanup = () => {
      connection.removeListener('readyForQuery', finish);
      connection.removeListener('end', finish);
      connection.removeListener('error', finish);
    };
    connection.once('readyForQuery', finish);
    connection.once('end', finish);
    connection.once('error', finish);
    let result;
    try { result = original.apply(client, args); }
    catch (error) { cleanup(); throw error; }
    try { return await result; }
    finally {
      if (client.readyForQuery || client._ending || client._ended || !client._queryable) finish();
      await ready;
      cleanup();
    }
  }

  async function execute(client, args, ctx, state) {
    if (typeof client.getTransactionStatus !== 'function') {
      throw new Error('Data actor bridge requires pg 8.22 or newer');
    }
    const status = client.getTransactionStatus();
    if (status !== 'I' && status !== 'T' && status !== 'E') {
      throw new Error('Connect the PostgreSQL client before querying');
    }
    if (status !== 'I' && state.transactionToken !== ctx.token) {
      throw new Error('A transaction belongs to a different request actor');
    }
    if (status === 'E') {
      const sql = typeof args[0] === 'string' ? args[0] : args[0]?.text;
      // Only literal ROLLBACK is allowed without set_config in an aborted tx.
      if (typeof sql !== 'string' || !/^\s*ROLLBACK\s*;?\s*$/i.test(sql)) {
        throw new Error('Rollback the aborted transaction before querying');
      }
    } else {
      await invoke(client, ["SELECT set_config('omnia.actor_token', $1, false)", [ctx.token]]);
    }
    try {
      return await invoke(client, args);
    } finally {
      const after = client.getTransactionStatus();
      if (after === 'I') {
        state.transactionToken = null;
        // Do not leave the last user's credential on a pooled idle connection.
        if (!client._ending && !client._ended && client._queryable) {
          await invoke(client, ["SELECT set_config('omnia.actor_token', '', false)"]);
        }
      } else if (after === 'T' || after === 'E') {
        state.transactionToken = ctx.token;
      }
    }
  }

  Client.prototype.query = function(config, values, callback) {
    const ctx = context();
    let cb = typeof values === 'function' ? values : callback;
    if (config && typeof config === 'object' && typeof config.callback === 'function') {
      cb = config.callback;
      config = { ...config };
      delete config.callback;
    }
    const args = typeof values === 'function' || values === undefined ? [config] : [config, values];
    let state = clients.get(this);
    if (!state) {
      state = { tail: Promise.resolve(), transactionToken: null };
      clients.set(this, state);
    }
    const promise = state.tail.then(() => {
      if (config == null || typeof config.submit === 'function') {
        throw new Error('Streaming/query objects are not supported by the data actor bridge');
      }
      return execute(this, args, ctx, state);
    });
    state.tail = promise.catch(() => {});
    if (typeof cb === 'function') {
      promise.then(result => actor.run(ctx, () => cb(null, result)),
        error => actor.run(ctx, () => cb(error)));
      return undefined;
    }
    return promise;
  };
}

function patchPool(Pool) {
  if (!Pool?.prototype?.connect || patchedPools.has(Pool)) return;
  patchedPools.add(Pool);
  const connect = Pool.prototype.connect;
  Pool.prototype.connect = function(callback) {
    if (typeof callback !== 'function') return connect.call(this);
    const ctx = context();
    return connect.call(this, (...args) => actor.run(ctx, () => callback(...args)));
  };
}

const load = Module._load;
Module._load = function(request, parent, isMain) {
  const value = load.call(this, request, parent, isMain);
  if (request === 'pg' || value?.Client?.prototype?.getTransactionStatus) {
    patchClient(value.Client);
    patchPool(value.Pool);
  } else if (typeof value === 'function' && value.prototype?.getTransactionStatus) {
    patchClient(value);
  }
  return value;
};
"""
