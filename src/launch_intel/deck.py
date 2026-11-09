"""One-click executive deck (.pptx) for a title. Requires: pip install 'launch_intel[deck]' (python-pptx).
Refuses to build if the narrative fails claim verification, so a deck never carries an unverified number."""
from .metrics import build_facts, cumulative_series
from .narrator import fmt, ignore_list, render, template_narrative, verify

PAPER, INK, ACCENT, MUTED = (0xF2, 0xEA, 0xD8), (0x1C, 0x1A, 0x16), (0xC2, 0x41, 0x0C), (0x6B, 0x64, 0x55)


def build_deck(conn, title_id, path):
    try:
        from pptx import Presentation
        from pptx.chart.data import CategoryChartData
        from pptx.dml.color import RGBColor
        from pptx.enum.chart import XL_CHART_TYPE
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise RuntimeError("Deck export needs python-pptx: pip install 'launch_intel[deck]'") from exc
    row = conn.execute("SELECT id, name, genre FROM titles WHERE id=?", (title_id,)).fetchone()
    peers = [r["id"] for r in conn.execute("SELECT id FROM titles WHERE id<>?", (title_id,))]
    facts = build_facts(conn, title_id, peers)
    text = render(template_narrative(row["name"], facts, conn, title_id), facts)
    rep = verify(text, facts, ignore=ignore_list(conn, title_id, row["name"]))
    if not rep.ok:
        raise ValueError("narrative failed verification: " + "; ".join(rep.reasons()))
    sample = conn.execute("SELECT COUNT(*) c FROM posts WHERE title_id=? AND is_sample=1", (title_id,)).fetchone()["c"] > 0
    col = lambda t: RGBColor(*t)
    prs = Presentation(); prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    def slide(kicker, title):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        s.background.fill.solid(); s.background.fill.fore_color.rgb = col(PAPER)
        def box(x, y, w, h, txt, size, bold=False, color=INK, font="Georgia"):
            tf = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
            tf.word_wrap = True; p = tf.paragraphs[0]; r = p.add_run(); r.text = txt
            r.font.size, r.font.bold, r.font.name = Pt(size), bold, font; r.font.color.rgb = col(color)
            return tf
        box(0.6, 0.35, 8, 0.4, kicker.upper(), 11, True, ACCENT, "Courier New")
        box(0.6, 0.7, 12, 0.9, title, 32, True)
        if sample:
            box(0.6, 7.0, 12, 0.35, "SYNTHETIC DEMO DATA - not real platform data", 10, True, ACCENT, "Courier New")
        return s, box

    s, box = slide("Launch report", row["name"])
    box(0.6, 1.9, 11.6, 3.2, text, 20)
    box(0.6, 5.6, 11.6, 0.5, f"Claim check: {rep.n_numbers} numbers, all traced to computed facts", 12, False, MUTED, "Courier New")

    series = cumulative_series(conn, title_id)
    s, box = slide("Trajectory", "Cumulative views, all posts")
    if len(series) >= 2:
        daily = [(h, v) for h, v in series if h % 24 == 0] or series
        cd = CategoryChartData(); cd.categories = [f"d{int(h // 24)}" for h, _ in daily]; cd.add_series("Views", [v for _, v in daily])
        ch = s.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(0.6), Inches(1.7), Inches(12), Inches(5.1), cd).chart
        ch.has_legend = False; ch.has_title = False; ch.series[0].format.line.color.rgb = col(INK); ch.series[0].smooth = False
        ch.value_axis.tick_labels.number_format = '0.0,,"M"'; ch.value_axis.tick_labels.number_format_is_linked = False
        ch.value_axis.major_gridlines.format.line.color.rgb = col((0xCF, 0xC4, 0xA8))
        ch.category_axis.tick_labels.font.size = Pt(10); ch.value_axis.tick_labels.font.size = Pt(10)
    else:
        box(0.6, 2, 11, 1, "Not enough snapshots to chart yet.", 18, False, MUTED)

    s, box = slide("Competitive set", "Share of 7-day views")
    from .metrics import share_of_voice
    sov = share_of_voice(conn, [title_id, *peers], 168)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM titles")}
    top = sorted(sov.items(), key=lambda kv: -kv[1])[:8]
    if title_id in sov and title_id not in [t for t, _ in top]:
        top = top[:7] + [(title_id, sov[title_id])]
    if top:
        cd = CategoryChartData(); cd.categories = [names[t] for t, _ in top]; cd.add_series("Share", [v for _, v in top])
        ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.6), Inches(1.7), Inches(12), Inches(5.1), cd).chart
        ch.has_legend = False; ch.has_title = False; ch.category_axis.reverse_order = True
        ch.series[0].format.fill.solid(); ch.series[0].format.fill.fore_color.rgb = col((0xB8, 0xAE, 0x92))
        for i, (t, _) in enumerate(top):
            if t == title_id:
                pt = ch.series[0].points[i]; pt.format.fill.solid(); pt.format.fill.fore_color.rgb = col(ACCENT)
        ch.value_axis.major_gridlines.format.line.color.rgb = col((0xCF, 0xC4, 0xA8))
        ch.category_axis.tick_labels.font.size = Pt(11); ch.value_axis.tick_labels.font.size = Pt(10)
        ch.value_axis.tick_labels.number_format = "0%"; ch.value_axis.tick_labels.number_format_is_linked = False

    s, box = slide("Ledger", "Every number in this deck")
    rows = prs.slides[-1].shapes.add_table(len(facts) + 1, 3, Inches(0.6), Inches(1.7), Inches(12), Inches(0.3 * (len(facts) + 1))).table
    for j, t in enumerate(("Fact", "Value", "How computed")):
        rows.cell(0, j).text = t
    for i, f in enumerate(facts, 1):
        for j, t in enumerate((f.label, fmt(f), f.query)):
            rows.cell(i, j).text = t
    for ri, r in enumerate(rows.rows):
        for c in r.cells:
            c.fill.solid(); c.fill.fore_color.rgb = col(INK if ri == 0 else PAPER if ri % 2 else (0xEB, 0xE2, 0xCB))
            for p in c.text_frame.paragraphs:
                for run in p.runs:
                    run.font.size, run.font.name = Pt(11), "Georgia"
                    run.font.color.rgb = col(PAPER if ri == 0 else INK)

    s, box = slide("Method and limits", "How to read this")
    box(0.6, 1.8, 11.8, 4.8,
        "Views between snapshots are linearly interpolated; unobserved windows are left blank, never extrapolated.\n"
        "Share of voice is view-based within the tracked set only. Genre percentiles are unstable with few titles.\n"
        "Event alignment shows timing, not cause: a controlled repeat is needed to attribute lift.\n"
        "Anomalies use a robust (MAD) z-score on day-over-day growth; consecutive days merge into one episode.", 16)
    prs.save(path)
    return path
