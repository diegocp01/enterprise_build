# OpenAI Build Week submission package

This directory is the judge-facing ZeroHandoff package.

| Path | Purpose |
| --- | --- |
| `sandbox/echoledger/` | Prebuilt source, tests, and production build created by the final autonomous run |
| `media/zerohandoff-submission-demo.mp4` | Canonical under-three-minute narrated working-product demo |
| `media/echoledger-generated-demo.mp4` | Identical run-native demo retained under its product-specific name |
| `media/echoledger-preview.png` | Final application preview for the Devpost gallery |
| `evidence/` | Curated training, delivery, trust-lineage, audit, demo, and checksum evidence |
| `DEVPOST_SUBMISSION.md` | Ready-to-paste project description |
| `DEMO_SCRIPT.md` | Narration and visual sequence for the final submission video |
| `COMPLIANCE_CHECKLIST.md` | Rules and final user-owned submission actions |

Start with [`../JUDGE_GUIDE.md`](../JUDGE_GUIDE.md). Verify the complete package
from the repository root:

```bash
python3 scripts/submission_package.py verify
```

The canonical delivery evidence is from `step13_echoledger_live_20260718` and
continual-learning commit sequence 6. The original ten-round training snapshot
remains immutable and is included separately from the evolving inference state.
