"""PDF report generation utilities using ReportLab."""
from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .schemas import AlertRecord, DashboardAnalyticsResponse, ImpactResponse, ThermalEvent


def _get_styles():
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#0F172A"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#64748B"),
        spaceAfter=12,
    )
    heading2_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1E293B"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#334155"),
    )
    body_bold = ParagraphStyle(
        "BodyDarkBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#0F172A"),
    )
    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#FFFFFF"),
    )
    return {
        "title": title_style,
        "subtitle": subtitle_style,
        "heading": heading2_style,
        "body": body_style,
        "body_bold": body_bold,
        "th": table_header_style,
    }


def generate_incidents_pdf(events: list[ThermalEvent], stats: DashboardAnalyticsResponse | None = None) -> bytes:
    """Generate a comprehensive incidents intelligence summary PDF."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = _get_styles()
    story = []

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Header Title
    story.append(Paragraph("PyroLens Intelligence Platform", styles["title"]))
    story.append(Paragraph(f"Thermal & Fire Incident Report &bull; Generated: {now_str}", styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#E11D48"), spaceBefore=2, spaceAfter=12))

    # Executive Summary / KPIs if stats provided
    if stats:
        story.append(Paragraph("Executive Overview", styles["heading"]))
        k = stats.kpis
        kpi_data = [
            [
                Paragraph("<b>Total Incidents Analyzed</b>", styles["body"]),
                Paragraph(str(k.total_incidents), styles["body_bold"]),
                Paragraph("<b>Active Detections</b>", styles["body"]),
                Paragraph(str(k.active_thermal_events), styles["body_bold"]),
            ],
            [
                Paragraph("<b>High / Critical Risk Zones</b>", styles["body"]),
                Paragraph(f"{k.high_risk_zones} ({k.critical_events} Critical)", styles["body_bold"]),
                Paragraph("<b>Avg Satellite Confidence</b>", styles["body"]),
                Paragraph(f"{k.avg_confidence}%", styles["body_bold"]),
            ],
            [
                Paragraph("<b>Total Observed FRP</b>", styles["body"]),
                Paragraph(f"{k.total_frp_mw:,.1f} MW", styles["body_bold"]),
                Paragraph("<b>Avg Fire Intensity</b>", styles["body"]),
                Paragraph(f"{k.avg_frp_mw:,.1f} MW", styles["body_bold"]),
            ],
        ]
        kpi_table = Table(kpi_data, colWidths=[140, 120, 140, 120])
        kpi_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ])
        )
        story.append(kpi_table)
        story.append(Spacer(1, 14))

    # Incidents Table
    story.append(Paragraph(f"Incident Records (Showing {len(events)} events)", styles["heading"]))

    table_data = [
        [
            Paragraph("ID", styles["th"]),
            Paragraph("Type / Class", styles["th"]),
            Paragraph("Location", styles["th"]),
            Paragraph("Lat, Lng", styles["th"]),
            Paragraph("Risk", styles["th"]),
            Paragraph("Score", styles["th"]),
            Paragraph("FRP (MW)", styles["th"]),
            Paragraph("Conf.", styles["th"]),
            Paragraph("Status", styles["th"]),
        ]
    ]

    risk_badge_colors = {
        "CRITICAL": colors.HexColor("#FEE2E2"),
        "HIGH": colors.HexColor("#FFEDD5"),
        "MEDIUM": colors.HexColor("#FEF3C7"),
        "LOW": colors.HexColor("#DCFCE7"),
    }

    for ev in events[:200]:  # Cap at 200 rows for PDF document size
        loc_display = ev.location[:24] + "..." if len(ev.location) > 24 else (ev.location or "Coordinates")
        type_display = ev.event_type[:20] + "..." if len(ev.event_type) > 20 else ev.event_type
        table_data.append([
            Paragraph(ev.id, styles["body"]),
            Paragraph(type_display, styles["body"]),
            Paragraph(loc_display, styles["body"]),
            Paragraph(f"{ev.latitude:.3f}, {ev.longitude:.3f}", styles["body"]),
            Paragraph(ev.risk_level, styles["body_bold"]),
            Paragraph(str(int(ev.risk_score)), styles["body"]),
            Paragraph(f"{ev.frp_mw:.1f}", styles["body"]),
            Paragraph(f"{int(ev.confidence)}%", styles["body"]),
            Paragraph(ev.status, styles["body"]),
        ])

    col_widths = [55, 80, 110, 80, 48, 35, 45, 35, 45]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    
    table_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94A3B8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
    ]

    for idx, ev in enumerate(events[:200], start=1):
        bg = colors.HexColor("#F8FAFC") if idx % 2 == 0 else colors.HexColor("#FFFFFF")
        table_styles.append(("BACKGROUND", (0, idx), (-1, idx), bg))
        if ev.risk_level in risk_badge_colors:
            table_styles.append(("BACKGROUND", (4, idx), (4, idx), risk_badge_colors[ev.risk_level]))

    table.setStyle(TableStyle(table_styles))
    story.append(table)

    story.append(Spacer(1, 14))
    story.append(Paragraph("Confidential &bull; PyroLens Operational Fire Intelligence Feed", styles["subtitle"]))

    doc.build(story)
    return buffer.getvalue()


def generate_incident_dossier_pdf(
    event: ThermalEvent,
    impact: ImpactResponse | None = None,
    alerts: list[AlertRecord] | None = None,
) -> bytes:
    """Generate an in-depth operational dossier PDF for a single incident."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = _get_styles()
    story = []

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Header
    story.append(Paragraph("PyroLens Incident Intelligence Dossier", styles["title"]))
    story.append(Paragraph(f"Incident: <b>{event.id}</b> &bull; Exported: {now_str}", styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#E11D48"), spaceBefore=2, spaceAfter=12))

    # General Attributes
    story.append(Paragraph("Thermal Observation Overview", styles["heading"]))
    overview_data = [
        [Paragraph("<b>Incident Name:</b>", styles["body"]), Paragraph(event.name, styles["body_bold"])],
        [Paragraph("<b>Location Description:</b>", styles["body"]), Paragraph(event.location or "Satellite Coordinates", styles["body"])],
        [Paragraph("<b>Coordinates (WGS84):</b>", styles["body"]), Paragraph(f"Lat {event.latitude:.5f}&deg;, Lng {event.longitude:.5f}&deg;", styles["body"])],
        [Paragraph("<b>Classification:</b>", styles["body"]), Paragraph(event.event_type, styles["body_bold"])],
        [Paragraph("<b>Risk Assessment:</b>", styles["body"]), Paragraph(f"{event.risk_level} (Score: {event.risk_score:.0f}/100)", styles["body_bold"])],
        [Paragraph("<b>Current Operational Status:</b>", styles["body"]), Paragraph(event.status, styles["body"])],
        [Paragraph("<b>Fire Radiative Power (FRP):</b>", styles["body"]), Paragraph(f"{event.frp_mw:.1f} MW", styles["body"])],
        [Paragraph("<b>Brightness Temperature:</b>", styles["body"]), Paragraph(f"{event.brightness_kelvin:.1f} K", styles["body"])],
        [Paragraph("<b>Persistence Score:</b>", styles["body"]), Paragraph(f"{event.persistence_score:.0f}/100", styles["body"])],
        [Paragraph("<b>Detection Confidence:</b>", styles["body"]), Paragraph(f"{event.confidence:.0f}%", styles["body"])],
        [Paragraph("<b>Observation Sensor:</b>", styles["body"]), Paragraph(f"{event.source} (Observed at {event.observed_at.strftime('%Y-%m-%d %H:%M UTC')})", styles["body"])],
    ]
    overview_table = Table(overview_data, colWidths=[160, 360])
    overview_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    story.append(overview_table)
    story.append(Spacer(1, 14))

    # Impact and Weather Analysis if available
    if impact:
        story.append(Paragraph("Impact & Atmospheric Spread Model", styles["heading"]))
        exp = impact.exposed
        weather = exp.get("weather", {}) if isinstance(exp, dict) else {}
        impact_data = [
            [Paragraph("<b>Spread Direction:</b>", styles["body"]), Paragraph(str(impact.direction), styles["body_bold"])],
            [Paragraph("<b>Estimated Impact Zone:</b>", styles["body"]), Paragraph(f"{impact.impact_zone_km2:.2f} km&sup2;", styles["body_bold"])],
            [Paragraph("<b>Impact Confidence:</b>", styles["body"]), Paragraph(f"{impact.confidence:.1f}%", styles["body"])],
            [Paragraph("<b>Wind Speed:</b>", styles["body"]), Paragraph(f"{weather.get('wind_speed_kmh', 'N/A')} km/h", styles["body"])],
            [Paragraph("<b>Ambient Temperature:</b>", styles["body"]), Paragraph(f"{weather.get('temperature_c', 'N/A')} &deg;C", styles["body"])],
            [Paragraph("<b>Relative Humidity:</b>", styles["body"]), Paragraph(f"{weather.get('humidity_pct', 'N/A')}%", styles["body"])],
            [Paragraph("<b>Spread Geometry Model:</b>", styles["body"]), Paragraph(impact.model, styles["body"])],
            [Paragraph("<b>GeoJSON Boundary:</b>", styles["body"]), Paragraph("Generated & Available in API", styles["body"])],
        ]
        impact_table = Table(impact_data, colWidths=[160, 360])
        impact_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ])
        )
        story.append(impact_table)
        story.append(Spacer(1, 14))

    # Associated Alerts History
    if alerts:
        story.append(Paragraph(f"Dispatched Alerts ({len(alerts)})", styles["heading"]))
        alert_rows = [
            [
                Paragraph("Alert ID", styles["th"]),
                Paragraph("Severity", styles["th"]),
                Paragraph("Status", styles["th"]),
                Paragraph("Channels", styles["th"]),
                Paragraph("Dispatched At", styles["th"]),
            ]
        ]
        for a in alerts:
            alert_rows.append([
                Paragraph(a.id[:8] + "...", styles["body"]),
                Paragraph(a.severity, styles["body_bold"]),
                Paragraph(a.status.upper(), styles["body"]),
                Paragraph(", ".join(a.channels), styles["body"]),
                Paragraph(a.created_at.strftime("%Y-%m-%d %H:%M"), styles["body"]),
            ])
        alert_table = Table(alert_rows, colWidths=[100, 80, 80, 120, 140])
        alert_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ])
        )
        story.append(alert_table)
        story.append(Spacer(1, 14))

    story.append(Paragraph("PyroLens Decision Support &bull; Operational Satellite Fire Intelligence", styles["subtitle"]))

    doc.build(story)
    return buffer.getvalue()

