export type AuthFormState = { error: string | null; refreshRequired?: boolean };
type AuthAction = (previous: AuthFormState, data: FormData) => Promise<AuthFormState>;

/** A stale page may reference an action removed by a new web deployment.
 * Do not replay credentials or swallow redirect/other application errors. */
export function recoverAuthAction(action: AuthAction): AuthAction {
  return async (previous, data) => {
    try {
      return await action(previous, data);
    } catch (error) {
      if (
        error instanceof Error &&
        (error.name === "UnrecognizedActionError" ||
          /^Server Action "[^"]+" was not found on the server\./.test(error.message))
      ) {
        return {
          error: "Страница обновилась на сервере. Обновите её и войдите снова.",
          refreshRequired: true,
        };
      }
      throw error;
    }
  };
}
