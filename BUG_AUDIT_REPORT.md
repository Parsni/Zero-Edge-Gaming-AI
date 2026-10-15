# Traceframe bug audit report

**Updated:** 2026-10-15  
**Scope:** source review and targeted runtime checks of the local prototype.  
**Status:** high-impact reproducible bugs fixed; important research and deployment limits remain.

## Fixes made

| Finding | Change | Verification |
|---|---|---|
| Desktop capture bypassed the web login because it loaded `file://` while API endpoints now require a session. | Electron now loads `/capture/` from the local API origin. The launcher passes the selected runtime URL into Electron. | **[PROVEN]** Authenticated route smoke check. **[CODE READ]** Electron host wiring and JS syntax. |
| Client-supplied chunk hashes were only chained together; the server did not establish that they described the submitted media. | Each signed link now declares its byte length. The server checks ordered indexes, complete file coverage, actual SHA-256 of each byte range, entropy digest, chain length, and root before accepting the request. | **[PROVEN]** Valid synthetic pipeline passes; malformed chain is rejected. Altered processed MP4 is rejected by receipt audit. |
| Desktop audit UI inserted verifier response strings as HTML. | Results and error details are rendered with DOM text nodes. | **[CODE READ]** JS syntax check and result-rendering review. |
| Oversized uploads could consume unbounded memory/disk. | Processing and verification read at most 512 MiB plus one sentinel byte, then return HTTP 413. | **[CODE READ]** Limit behavior is present; 512 MiB boundary was not stress-tested. |
| Invalid JSON/auth request shapes and Unicode username behavior could cause inconsistent errors. | Registration/login return clear HTTP 400s for malformed bodies; account name validation now matches ASCII help text. | **[CODE READ]** Source review; registration success exercised, malformed auth bodies not separately exercised. |
| Admin changes could remove the last active administrator. | Disable/demote/delete now preserve at least one active admin. | **[CODE READ]** Guard added; no multi-admin lifecycle scenario exercised. |
| Gemini crop response was clamped before validating basic bounds, which could conceal malformed model output. | Out-of-range crop coordinates/dimensions and invalid time ranges trigger local motion fallback. Gemini is now opt-in rather than the default. | **[CODE READ]** Source validation. **[PROVEN]** Current synthetic processing recorded `local_motion_heuristic` without calling Gemini. |
| Launcher could attach to an older API without checking build identity. | Electron launcher now searches ports 8007–8017 for runtime build 2.6.0 and passes the selected API URL to the app. | **[CODE READ]** Launcher updated; runtime is restarted on the latest build after final edits. |
| Processing depended on Gemini/network and failed over to an uninformative center crop. | Default crop selection now uses a local FFmpeg frame-difference heuristic to choose a five-second high-motion interval and crop position. The receipt records the method. Gemini is opt-in and failures fall back locally. | **[PROVEN]** Local heuristic selected coordinates on a synthetic video fixture without a model call. **[CODE READ]** Gemini is opt-in only when `CROP_PROVIDER=gemini`. |
| Public landing page, styles and featured sample were inaccessible to guests. | Public GET `/`, read-only `/site/*` assets, and one allowlisted `/demo-video` are available without an account. Personal output, processing, and audit routes still require login. | **[PROVEN]** Guest HTTP checks returned 200 for the page/assets/sample and 401/redirect for protected routes. |

## Remaining issues and limits

- **Identity:** edge signing keys are ephemeral and self-asserted. The server verifies a signature, not a person, physical device, or genuine gameplay.
- **Telemetry:** mouse/keyboard/frame timing is client supplied and can be fabricated. The synthetic demo uses fixed values.
- **Time semantics:** chunk hashes are now bound to actual uploaded byte ranges, but byte ranges are not decoded-frame boundaries and do not prove event-to-pixel timing.
- **Model semantics:** default crop is a local pixel-motion heuristic, not an AI/ML model; it does not recognize kills, players, UI, or highlight semantics. Gemini is optional. There is no model-authenticity or deepfake classifier.
- **AWS:** S3 archive and Lambda hook are optional and disabled until credentials/configuration are provided. This is not a deployed serverless service.
- **Blockchain:** no chain transactions or zero-knowledge protocol are implemented.
- **Public exposure:** this prototype is for trusted local/LAN use. It has no production account recovery, registration invitation, abuse throttling, or hosted operational controls. Do not port-forward it to the internet.
- **Automated ML metric:** the 50% metadata-only baseline is on a small, paired synthetic byte-flip experiment, not representative gameplay or AI-generated media. The crypto result means only that this verifier detects the controlled mutations in this fixture set.

## Verification record

Targeted checks were run for this change because the request was to fix bugs. **[PROVEN]** A synthetic source video was signed, processed, and audited; the untouched MP4 passed, a byte-flipped MP4 failed, a malformed chain failed, a cross-origin registration write was rejected (403), and an over-limit upload was rejected (413 using a reduced test limit). Guest HTTP checks returned 200 for the landing page, assets and featured demo, and 401 or sign-in redirect for source files, capture, arbitrary outputs and audits. Local motion crop selected a valid 9:16 crop without making a Gemini call. The regenerated research pack audited eleven stored receipts (22 paired trials): the byte-integrity verifier agreed with these controlled mutation labels on all trials; the metadata-only baseline scored 50% holdout accuracy. Those metrics describe this narrow fixture only. **[CODE READ]** Electron login/session wiring and launcher selection. No external AWS account, successful Gemini model response, native screen-picker UI, or device-attestation hardware was available to validate those integrations.
