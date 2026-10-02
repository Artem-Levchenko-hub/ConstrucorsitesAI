"""Read-only serving health and actual gateway pool feature diagnostic."""
import asyncio,json,urllib.request
from yleum_gateway.core.db import init_pool,get_pool,close_pool
async def main():
 result={'readOnly':True,'queries':{}}
 try:
  with urllib.request.urlopen('http://127.0.0.1:8001/health',timeout=10) as r:
   result['liveHealth']={'http':r.status,'status':json.load(r).get('status')}
  await init_pool()
  async with get_pool().acquire() as c:
   queries={
    'migrationMarker':'SELECT EXISTS(SELECT 1 FROM alembic_version)',
    'settlementTable':"SELECT to_regclass('public.usage_settlements') IS NOT NULL",
    'columns':'SELECT id,user_id,billing_account_id,provider_scope,provider_request_id,receipt_hash,usage_id,wallet_charge_id,status FROM usage_settlements LIMIT 0',
    'actualConstraints':"SELECT conname,contype,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='public.usage_settlements'::regclass ORDER BY conname"}
   for name,sql in queries.items():
    try:
     async with c.transaction(readonly=True):
      rows=await c.fetch(sql)
      result['queries'][name]={'ok':True,'rows':[dict(r) for r in rows]}
    except Exception as e:
     result['queries'][name]={'ok':False,'errorType':type(e).__name__,'sqlstate':getattr(e,'sqlstate',None)}
 except Exception as e:
  result['blocked']={'errorType':type(e).__name__,'sqlstate':getattr(e,'sqlstate',None)}
 finally:
  await close_pool()
 print(json.dumps(result, default=lambda value: value.decode("ascii") if isinstance(value, bytes) else str(value)))
asyncio.run(main())
