---
title: "VPS Setup for Omnia V2 Phase A Runtime"
tags: [devops, docker, nginx, production, runtime, setup, vps]
sources:
  - daily/2026-05-20.md
created: 2026-05-20
updated: 2026-05-20
---

# VPS Setup for Omnia V2 Phase A Runtime

The VPS Setup document (08-vps-setup.md) provides specific instructions for configuring the `170.168.72.200` Serverum VPS to host the Omnia V2 orchestrator and user dev-containers. It details the server's current state and outlines manual setup steps to be performed by Artyom via SSH.

## Key Points

- Prepares `170.168.72.200` (inquisitive-head) for V2 orchestrator and dev-containers.
- Setup is manual, performed by Artyom via SSH.
- Server runs Debian 12, kernel 6.1.170, supporting Rootless Docker.
- 8 cores / 15 GB RAM (10 GB free) sufficient for 10-15 dev-containers.
- Significant disk space (380 GB) occupied by Docker garbage, requiring cleanup.
- Uses system Nginx on :80/443, requiring orchestrator to manage `sites-enabled`.

## Details

This document is a practical guide for setting up the production VPS for Omnia V2 Phase A. It emphasizes that all operations are manual and executed by Artyom via SSH, highlighting that Claude Code does not directly modify production environments. This ensures human oversight and control over critical infrastructure changes.

A detailed snapshot of the server's current state (as of 2026-05-18) is provided, covering OS, kernel, CPU/RAM, disk usage, Docker version, and existing user configurations. This context is crucial for understanding how V2 components will integrate with the existing environment.

Key observations include the server's Debian 12 OS with a kernel supporting Rootless Docker, ample CPU and RAM for the expected number of dev-containers, and a significant amount of disk space consumed by Docker garbage, which necessitates a cleanup step. The presence of a system Nginx instance means the orchestrator will need to dynamically configure `sites-enabled` for V2 applications.

The document serves as a prerequisite for Agent D (Orchestrator) by providing the necessary environmental context and setup commands. It ensures that the physical infrastructure is ready to support the persistent dev-containers and the orchestrator service, which are central to the V2 architecture.

## Related Concepts

- [[knowledge/concepts/omnia-v2-architecture-phase-a]]
- [[knowledge/concepts/agent-d-orchestrator-devops-v2-phase-a]]

## Sources

- [[daily/2026-05-20.md]]
