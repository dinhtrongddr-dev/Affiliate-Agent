"""Performance Learning 2.0: Post-publish video analytics & creative feedback loop.

Tracks actual video metrics (views, 3s retention, completion rate, shares, comments, saves,
conversions) and extracts actionable insights to optimize hooks, storytelling formats,
and duration targets for subsequent production runs.
"""
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("affiliate_agent.performance_learning")


@dataclass
class VideoPerformanceMetrics:
    views: int = 0
    retention_3s_rate: float = 0.0       # 0.0 - 1.0 (e.g. 0.65 = 65% retained at 3s)
    avg_view_duration_sec: float = 0.0   # seconds
    completion_rate: float = 0.0         # 0.0 - 1.0
    replay_rate: float = 0.0             # 0.0 - 1.0
    shares: int = 0
    comments: int = 0
    saves: int = 0
    new_followers: int = 0
    clicks: int = 0
    conversions: int = 0
    commission_earned: float = 0.0


def init_performance_db(database_path: Path) -> None:
    """Ensure performance tracking table exists in sqlite."""
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS video_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                content_mode TEXT DEFAULT 'viral',
                hook_style TEXT,
                duration_category TEXT,
                metrics_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(job_id, platform)
            )
        """)
        con.commit()


def record_video_performance(database_path: Path, job_id: str, platform: str,
                             metrics: dict, content_mode: str = "viral",
                             hook_style: str = "", duration_category: str = "") -> None:
    """Save or update actual post-publish metrics for a job on a platform."""
    init_performance_db(database_path)
    with sqlite3.connect(database_path) as con:
        con.execute("""
            INSERT INTO video_performance (job_id, platform, content_mode, hook_style, duration_category, metrics_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, platform) DO UPDATE SET
                content_mode=excluded.content_mode,
                hook_style=excluded.hook_style,
                duration_category=excluded.duration_category,
                metrics_json=excluded.metrics_json,
                created_at=CURRENT_TIMESTAMP
        """, (job_id, platform, content_mode, hook_style, duration_category, json.dumps(metrics, ensure_ascii=False)))
        con.commit()


def get_video_performance(database_path: Path, job_id: str) -> list[dict]:
    """Retrieve all recorded metrics across platforms for a job."""
    init_performance_db(database_path)
    with sqlite3.connect(database_path) as con:
        rows = con.execute("""
            SELECT platform, content_mode, hook_style, duration_category, metrics_json, created_at
            FROM video_performance WHERE job_id = ?
        """, (job_id,)).fetchall()
        result = []
        for r in rows:
            data = json.loads(r[4])
            data.update(
                platform=r[0], content_mode=r[1], hook_style=r[2],
                duration_category=r[3], recorded_at=r[5]
            )
            result.append(data)
        return result


def analyze_performance_insights(database_path: Path) -> dict:
    """Extract performance insights across published videos to optimize future production.

    Analyzes:
    - Best performing hook styles by 3s retention.
    - Optimal duration categories by completion rate.
    - Highest engagement content modes.
    """
    init_performance_db(database_path)
    with sqlite3.connect(database_path) as con:
        rows = con.execute("""
            SELECT content_mode, hook_style, duration_category, metrics_json FROM video_performance
        """).fetchall()

    if not rows:
        return {
            "total_videos_analyzed": 0,
            "best_hook_style": "curiosity_gap",
            "optimal_duration_category": "story_viral",
            "average_3s_retention": 0.0,
            "average_completion_rate": 0.0,
            "recommendations": [
                "Chưa đủ dữ liệu thực tế sau xuất bản; áp dụng cấu trúc Story Viral 25-35s chuẩn.",
                "Ưu tiên hook có hành động thị giác ngay giây 0 và text hook <= 7 từ.",
            ],
        }

    hook_stats: dict[str, list[float]] = {}
    duration_stats: dict[str, list[float]] = {}
    all_retentions = []
    all_completions = []

    for r in rows:
        c_mode, h_style, dur_cat, m_json = r
        try:
            m = json.loads(m_json)
        except Exception:
            continue
        ret = float(m.get("retention_3s_rate") or 0.0)
        comp = float(m.get("completion_rate") or 0.0)
        if ret > 0:
            all_retentions.append(ret)
            if h_style:
                hook_stats.setdefault(h_style, []).append(ret)
        if comp > 0:
            all_completions.append(comp)
            if dur_cat:
                duration_stats.setdefault(dur_cat, []).append(comp)

    # Determine best hook
    best_hook = "curiosity_gap"
    best_hook_avg = 0.0
    for h, vals in hook_stats.items():
        avg = sum(vals) / len(vals)
        if avg > best_hook_avg:
            best_hook_avg = avg
            best_hook = h

    # Determine optimal duration
    best_dur = "story_viral"
    best_dur_avg = 0.0
    for d, vals in duration_stats.items():
        avg = sum(vals) / len(vals)
        if avg > best_dur_avg:
            best_dur_avg = avg
            best_dur = d

    avg_ret = round(sum(all_retentions) / len(all_retentions), 3) if all_retentions else 0.0
    avg_comp = round(sum(all_completions) / len(all_completions), 3) if all_completions else 0.0

    recommendations = []
    if best_hook_avg > 0:
        recommendations.append(
            f"Phong cách hook '{best_hook}' đang đạt tỷ lệ giữ chân 3s trung bình cao nhất ({best_hook_avg * 100:.1f}%)."
        )
    if best_dur_avg > 0:
        recommendations.append(
            f"Khoảng thời lượng '{best_dur}' đang đạt tỷ lệ xem hết cao nhất ({best_dur_avg * 100:.1f}%)."
        )
    recommendations.append("Duy trì mở đầu hành động trực quan và kết thúc câu chuyện trọn vẹn trước khi chuyển cảnh.")

    return {
        "total_videos_analyzed": len(rows),
        "best_hook_style": best_hook,
        "optimal_duration_category": best_dur,
        "average_3s_retention": avg_ret,
        "average_completion_rate": avg_comp,
        "recommendations": recommendations,
    }


def format_performance_feedback_for_prompt(database_path: Path) -> str:
    """Format performance insights into a prompt-ready directive string."""
    insights = analyze_performance_insights(database_path)
    if not insights.get("total_videos_analyzed"):
        return ""
    lines = [
        "### DỮ LIỆU HIỆU SUẤT THỰC TẾ ĐÃ ĐƯỢC CHỨNG MINH:",
        f"- Phong cách Hook tối ưu: {insights.get('best_hook_style')}",
        f"- Phân loại thời lượng tối ưu: {insights.get('optimal_duration_category')}",
    ]
    for rec in insights.get("recommendations", []):
        lines.append(f"- {rec}")
    return "\n".join(lines)

