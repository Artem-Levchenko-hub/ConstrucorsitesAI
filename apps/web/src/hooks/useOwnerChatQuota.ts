"use client";
import { useQuery } from "@tanstack/react-query";
import { getOwnerProfile } from "@/lib/api/owner-profile";
import { OWNER_PROFILE_QUERY_KEY, ownerChatQuota } from "@/lib/owner-chat-quota";

export function useOwnerChatQuota() {
  const profile = useQuery({ queryKey: OWNER_PROFILE_QUERY_KEY, queryFn: getOwnerProfile,
    staleTime: 0, retry: false });
  return { ...ownerChatQuota(profile.data), refresh: profile.refetch };
}
