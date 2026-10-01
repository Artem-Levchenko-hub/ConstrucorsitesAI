import assert from "node:assert/strict";
import test from "node:test";
import { sql } from "drizzle-orm";
import { PgDialect } from "drizzle-orm/pg-core";

const dialect = new PgDialect();

test("PostgreSQL identifier delimiters stay inside one quoted identifier", () => {
  for (const identifier of ["ordinary", 'two"quotes"', 'name"; SELECT 7; --', 'юникод"имя']) {
    const query = dialect.sqlToQuery(sql`select ${sql.identifier(identifier)}`);
    // GHSA-gpj5-g38j-94v9: a delimiter from runtime input must be doubled,
    // rather than terminating the identifier and becoming another statement.
    assert.equal(query.sql, `select "${identifier.replaceAll('"', '""')}"`);
    assert.deepEqual(query.params, []);
  }
});

test("Hostile values remain driver parameters alongside escaped identifiers", () => {
  const identifier = 'qa"column';
  const value = "'; DROP TABLE max_users; --";
  const query = dialect.sqlToQuery(sql`select ${sql.identifier(identifier)} where ${sql.identifier("value")} = ${value}`);
  assert.equal(query.sql, 'select "qa""column" where "value" = $1');
  assert.deepEqual(query.params, [value]);
  assert.ok(!query.sql.includes(value));
});
