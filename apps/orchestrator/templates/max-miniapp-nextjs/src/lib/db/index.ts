import { sql } from "drizzle-orm";
import { drizzle } from "drizzle-orm/node-postgres";
import { Pool } from "pg";

import * as schema from "./schema";

if (!process.env.DATABASE_URL) {
  throw new Error("DATABASE_URL is required");
}

export const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 10,
  idleTimeoutMillis: 30_000,
});

export const db = drizzle(pool, { schema });
export { schema };

export type MaxUserTx = Parameters<Parameters<typeof db.transaction>[0]>[0];

/**
 * Любое обращение к данным конкретного пользователя идёт через эту обёртку.
 *
 * Внутри открывается транзакция и в ней выставляется app.max_user_id — по нему
 * работают политики уровня строк (drizzle/0002_row_level_security.sql). База
 * сама не отдаёт и не принимает чужие строки, поэтому фильтр в коде остаётся
 * второй линией, а не единственной.
 *
 * Важное свойство: если новый маршрут забудет эту обёртку, личность не будет
 * выставлена и запрос вернёт ПУСТО, а не чужие данные. Забывчивость становится
 * заметной поломкой вместо тихой утечки.
 *
 * `set_config(..., true)` действует только до конца транзакции, поэтому
 * личность не протекает на соседний запрос через общее соединение пула.
 */
export async function withMaxUser<T>(
  maxUserId: string,
  run: (tx: MaxUserTx) => Promise<T>,
): Promise<T> {
  if (!maxUserId) {
    // Пустая личность прошла бы политику как «не задано» и дала бы пустой ответ.
    // Громкая ошибка вызова лучше маршрута, который молча ничего не находит.
    throw new Error("withMaxUser requires a non-empty max user id");
  }
  return db.transaction(async (tx) => {
    await tx.execute(sql`select set_config('app.max_user_id', ${maxUserId}, true)`);
    return run(tx);
  });
}
