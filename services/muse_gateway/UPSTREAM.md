# Embedded muse2api gateway

Source: https://github.com/czg86389-hub/muse2api, tag v1.6.10 (commit b60f474), MIT License (see LICENSE).

Only the runtime files are embedded. The upstream tests, Docker files, browser extension and deploy scripts are left out.

AffiliateAgent starts this gateway as a child process; see `agent_core/videogen/gateway.py`.
- It listens on 127.0.0.1 only.
- Its data (account session, tasks, media, Chrome profile) lives in `state/muse2api/`, which is git-ignored.
- It drives its own headless Chrome profile and never touches the user's Chrome.

Local changes:
1. `app.py`, `_silent_upgrade_worker`: the automatic self-upgrade is disabled. Upstream pulls `main` from GitHub and restarts whenever `/admin/update/check` is called, and that endpoint needs no authentication. Set `MUSE2API_ALLOW_AUTO_UPGRADE=1` to restore upstream behaviour.

To upgrade:
1. Review the upstream diff.
2. Copy the same files from the new tag.
3. Re-apply the patch above.
