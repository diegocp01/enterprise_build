# OpenAI Build Week submission package

This directory is the judge-facing ZeroHandoff package.

| Path | Purpose |
| --- | --- |
| `sandbox/coveragecanvas/` | Prebuilt source, tests, and production build created by the canonical autonomous run |
| `media/zerohandoff-submission-demo.mp4` | Under-three-minute hackathon overview and working-product demo |
| `media/coveragecanvas-generated-demo.mp4` | Narrated demo generated autonomously as part of the delivery bundle |
| `media/coveragecanvas-preview.png` | Canonical application preview for the Devpost gallery |
| `evidence/` | Curated training, delivery, trust-lineage, audit, demo, and checksum evidence |
| `DEVPOST_SUBMISSION.md` | Ready-to-paste project description |
| `DEMO_SCRIPT.md` | Narration and visual sequence for the final submission video |
| `COMPLIANCE_CHECKLIST.md` | Rules and final user-owned submission actions |

Start with [`../JUDGE_GUIDE.md`](../JUDGE_GUIDE.md). Verify the complete package
from the repository root:

```bash
python3 scripts/submission_package.py verify
```
