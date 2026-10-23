"""Read ONE block of a transcript with the vision model and print what it returned. Calls the real model (Ollama) once per block: for teammates diagnosing reading quality.

    python scripts/read_test.py <mac_dev|mac_quality> <pdf> <page 1-based> <block numbers...>
    python scripts/read_test.py mac_dev data/demo_inputs/demo2.pdf 1 3 4

Blocks are numbered from 0 in the order the layout code returns them (side-by-side tables are read column by column). Synthetic files only."""
import asyncio
import sys

from backend.app.config import get_settings
from backend.app.providers.router import build_provider
from backend.app.services import vision_reader as vr

route, path, page, blocks = sys.argv[1], sys.argv[2], int(sys.argv[3]), [int(b) for b in sys.argv[4:]]
settings = get_settings()
settings.llm_default_route = route
provider = build_provider(settings)
img = vr.render_pages(path, settings.vision_dpi)[page - 1]
layout = vr.layout(img, settings.vision_max_block_px)
print("model:", provider.model, "| blocks on this page:", len(layout))


async def main() -> None:
    for i in blocks:
        span, xr, _ = layout[i]
        try:
            b = await vr.read_block(provider, vr.crop_png_b64(img, span, 1.0, xr), f"b{i}")
            print(i, span, xr, [(r.subject, r.mark, r.letter, r.credits) for r in b.rows], "totals:", b.term_credits, b.term_quality_points, b.term_gpa)
        except Exception as e:  # noqa: BLE001
            print(i, "ERROR", type(e).__name__, e)

asyncio.run(main())
