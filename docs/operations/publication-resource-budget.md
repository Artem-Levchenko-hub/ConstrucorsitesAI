# Published MAX app scheduling budget

The small-app Kubernetes profile requests 350m CPU across seven pods at the
production app limit of 1 CPU. Requests reserve scheduling capacity and CPU
shares under contention; they do not cap actual use. Existing CPU limits, RAM
requests/limits, storage, replicas and isolation stay unchanged.

| Component | CPU request |
| --- | ---: |
| App | 200m |
| Core | 50m |
| Core Postgres | 25m |
| Project Postgres | 25m |
| Redis | 10m |
| Analytics relay | 10m |
| Boundary | 30m |
| **Total** | **350m** |

Seeding and database bootstrap init containers request 25m, so their peak does
not raise the pod reservation above the running containers. For an explicitly
smaller app CPU cap, the app request remains at most half that cap (with the
existing 50m floor).

At this profile, 20 published apps request 7 CPU and 21.5625 GiB RAM across 140
pods. With approximately 0.3 CPU of system requests measured on the existing
8-CPU runtime, scheduling leaves approximately 0.7 CPU. Recheck actual node
allocatable resources, system pods, pod limits and disk capacity before admission.
These figures cover one replica per published component, not concurrent
generations, rollout surge, heavy visitor load, or a performance guarantee.

The previous 1-CPU app profile requested 780m in total. Live idle observations on
2026-11-16 were approximately 40–44m per app; this motivated reducing requests,
while retaining burst limits. Validate latency and CPU contention with real load
as clients are added. Increase requests for busy applications if needed.

The profile is generated in `k8s_publication.py`. New publications receive it;
existing pods require reconciliation/republication to update their requests.
The manifest regression test counts the maximum of regular-container requests
and init-container requests per pod, and preserves memory and CPU caps.
