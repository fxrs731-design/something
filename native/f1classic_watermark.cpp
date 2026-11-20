#include <algorithm>

#include <swofty/mixin.hpp>
#include <swofty/overlay.hpp>

namespace {

void paint_watermark(SwoftyPaint *paint, void *)
{
    const auto safe = swofty::overlay::safe_area(paint);
    const float scale = std::clamp(swofty::overlay::ui_scale(paint), 0.75f, 1.5f);

    swofty::overlay::TextStyle style;
    style.size = 16.0f * scale;
    style.rgba = 0xFFFFFFE6;
    style.align = swofty::overlay::align::left | swofty::overlay::align::bottom;
    style.outline = 0x000000B3;
    style.outline_px = 2.0f * scale;

    const float margin = 14.0f * scale;
    swofty::overlay::text(
        paint,
        safe.x + margin,
        safe.y + safe.h - margin,
        style,
        "F1 CLASSIC LAUNCHER");
}

}

SWOFTY_ON_LOAD
{
    if (!swofty::overlay::available()) {
        swofty::log("[f1classic] in-game overlay API is unavailable");
        return;
    }

    const auto result = swofty::overlay::on_paint(paint_watermark);
    swofty::log("[f1classic] watermark registration: %s", swofty::overlay::name(result));
}

SWOFTY_ADDON()
