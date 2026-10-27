import { apiFetch } from "./client";

export function getAccountCredentials() {
  return apiFetch<{ has_password: boolean }>("/api/account/credentials");
}

export function changeAccountPassword(currentPassword: string, newPassword: string) {
  return apiFetch<{ ok: true; reauthenticate: true }>("/api/account/credentials/password", {
    method: "POST", json: { current_password: currentPassword, new_password: newPassword },
  });
}

export function requestAccountEmailChange(currentPassword: string, newEmail: string) {
  return apiFetch<{ status: "confirmation_sent" }>("/api/account/credentials/email", {
    method: "POST", json: { current_password: currentPassword, new_email: newEmail.trim() },
  });
}

export function confirmAccountEmailChange(token: string) {
  return apiFetch<{ status: "pending" | "completed"; reauthenticate: boolean }>("/api/account/credentials/email/confirm", {
    method: "POST", json: { token }, referrerPolicy: "no-referrer",
  });
}
