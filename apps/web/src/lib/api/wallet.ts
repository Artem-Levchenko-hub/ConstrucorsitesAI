import { apiFetch } from "./client";
import { mockApi, USE_MOCKS } from "./mocks";
import type { WalletState } from "./types";

export async function getWallet(): Promise<WalletState> {
  if (USE_MOCKS) return mockApi.getWallet();
  return apiFetch<WalletState>("/api/wallet");
}
