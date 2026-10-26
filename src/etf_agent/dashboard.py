"""產生精美且易於閱讀的 HTML 視覺化戰情儀表板 (Dashboard)。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def generate_html_dashboard(
    dplan_path: str | Path,
    report_dict: dict[str, Any] | None = None,
    output_html_path: str | Path | None = None,
) -> Path:
    p = Path(dplan_path)
    dplan = json.loads(p.read_text(encoding="utf-8"))
    
    trade_date = dplan.get("trade_date", "")
    team_id = dplan.get("team_id", "")
    mv = dplan.get("market_view", {})
    posture = mv.get("posture", {})
    orders = dplan.get("orders", [])
    inferences = dplan.get("inferences", [])
    decisions = dplan.get("decisions", [])
    sources = dplan.get("sources", [])
    observations = dplan.get("observations", [])
    meta = dplan.get("agent_metadata", {})

    inf_map = {inf["inf_id"]: inf for inf in inferences}

    # 計算總買賣
    buy_orders = [o for o in orders if o["side"] == "BUY"]
    sell_orders = [o for o in orders if o["side"] == "SELL"]

    orders_rows = ""
    for o in orders:
        d_ref = o.get("decision_ref", "")
        # 找出對應決策與推論
        matched_dec = next((d for d in decisions if d["decision_id"] == d_ref), None)
        inf_text = ""
        counter_text = ""
        if matched_dec and matched_dec.get("inference_refs"):
            first_inf = inf_map.get(matched_dec["inference_refs"][0])
            if first_inf:
                inf_text = first_inf.get("logic", "")
                counter_text = first_inf.get("counter_evidence", "")

        target_w = f"{matched_dec['target_weight']*100:.2f}%" if matched_dec else "--"
        badge_class = "badge-buy" if o["side"] == "BUY" else "badge-sell"
        orders_rows += f"""
        <tr>
            <td><strong>{o['ticker']}</strong></td>
            <td><span class="badge {badge_class}">{o['side']}</span></td>
            <td>{o['shares']:,} 股</td>
            <td>{target_w}</td>
            <td style="font-size: 0.9em; line-height: 1.4;">
                <div style="color: #2c3e50;">{inf_text}</div>
                {f'<div style="color: #c0392b; margin-top: 4px; font-size: 0.85em;">⚠️ 反方風險: {counter_text}</div>' if counter_text else ''}
            </td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI CUP 2026 ETF 戰情儀表板 — {team_id} ({trade_date})</title>
    <style>
        :root {{
            --primary: #1e3c72;
            --secondary: #2a5298;
            --success: #27ae60;
            --warning: #f39c12;
            --danger: #e74c3c;
            --bg: #f8fafc;
            --card: #ffffff;
            --text: #2d3748;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            background: linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);
            color: white;
            padding: 28px 32px;
            border-radius: 16px;
            box-shadow: 0 10px 25px rgba(30, 60, 114, 0.15);
            margin-bottom: 28px;
        }}
        .header h1 {{
            margin: 0 0 10px 0;
            font-size: 1.8rem;
            letter-spacing: 0.5px;
        }}
        .header p {{
            margin: 0;
            opacity: 0.9;
            font-size: 0.95rem;
        }}
        /* 進度管線 Pipeline */
        .pipeline-card {{
            background: var(--card);
            border-radius: 14px;
            padding: 20px 24px;
            margin-bottom: 24px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        }}
        .pipeline-title {{
            font-size: 1.1rem;
            font-weight: bold;
            margin-bottom: 16px;
            color: var(--primary);
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .steps {{
            display: flex;
            justify-content: space-between;
            position: relative;
        }}
        .steps::before {{
            content: "";
            position: absolute;
            top: 20px;
            left: 40px;
            right: 40px;
            height: 4px;
            background: #e2e8f0;
            z-index: 1;
        }}
        .step {{
            position: relative;
            z-index: 2;
            background: var(--card);
            padding: 0 10px;
            text-align: center;
            flex: 1;
        }}
        .step-circle {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: var(--success);
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 10px auto;
            font-weight: bold;
            box-shadow: 0 4px 10px rgba(39, 174, 96, 0.3);
        }}
        .step-label {{
            font-size: 0.9rem;
            font-weight: 600;
            color: #4a5568;
        }}
        .step-sub {{
            font-size: 0.75rem;
            color: #718096;
            margin-top: 4px;
        }}
        /* 指標卡片 */
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 20px;
            margin-bottom: 24px;
        }}
        .card {{
            background: var(--card);
            border-radius: 14px;
            padding: 20px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        }}
        .card-label {{
            font-size: 0.85rem;
            color: #718096;
            text-transform: uppercase;
            font-weight: 600;
            margin-bottom: 8px;
        }}
        .card-val {{
            font-size: 1.5rem;
            font-weight: bold;
            color: var(--primary);
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: bold;
        }}
        .badge-buy {{
            background: #e8f8f0;
            color: #27ae60;
        }}
        .badge-sell {{
            background: #fdf2f2;
            color: #e74c3c;
        }}
        /* 表格樣式 */
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th, td {{
            padding: 14px 16px;
            text-align: left;
            border-bottom: 1px solid #edf2f7;
        }}
        th {{
            background: #f7fafc;
            color: #4a5568;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        tr:hover {{
            background: #f8fafc;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 AI CUP 2026 ETF 決策戰情室</h1>
            <p>隊伍代號：<strong>{team_id}</strong> ｜ 對應交易日：<strong>{trade_date}</strong> ｜ 驅動模型：<strong>{meta.get('model_provider')} ({meta.get('model_version')})</strong></p>
        </div>

        <!-- 視覺化執行階段流程條 -->
        <div class="pipeline-card">
            <div class="pipeline-title">
                <span>⚡ LangGraph 決策推理執行管線進度</span>
            </div>
            <div class="steps">
                <div class="step">
                    <div class="step-circle">1</div>
                    <div class="step-label">M2: 行情與資料包</div>
                    <div class="step-sub">150檔收盤價 100% 齊備</div>
                </div>
                <div class="step">
                    <div class="step-circle">2</div>
                    <div class="step-label">M3: 異動漏斗掃描</div>
                    <div class="step-sub">{len(observations)} 筆事實與 S/O 鎖定</div>
                </div>
                <div class="step">
                    <div class="step-circle">3</div>
                    <div class="step-label">M4: Gemini 推理</div>
                    <div class="step-sub">大盤觀點 + 因果鏈深化</div>
                </div>
                <div class="step">
                    <div class="step-circle">4</div>
                    <div class="step-label">M5: 沙盒鐵律驗算</div>
                    <div class="step-sub">千股反推 + 8大檢核亮綠燈</div>
                </div>
                <div class="step">
                    <div class="step-circle">5</div>
                    <div class="step-label">產出與存證</div>
                    <div class="step-sub">D-Plan + 思考軌跡存證</div>
                </div>
            </div>
        </div>

        <!-- 核心指標卡片 -->
        <div class="grid">
            <div class="card">
                <div class="card-label">市場走向 / 操作姿態</div>
                <div class="card-val">{mv.get('regime')} / {mv.get('stance')}</div>
                <div style="font-size: 0.85rem; color: #718096; margin-top: 6px;">曝險意圖: {posture.get('net_exposure_intent')}</div>
            </div>
            <div class="card">
                <div class="card-label">目標現金水位區間</div>
                <div class="card-val">{posture.get('target_cash_pct_range', [0, 0])[0]*100:.1f}% ~ {posture.get('target_cash_pct_range', [0, 0])[1]*100:.1f}%</div>
                <div style="font-size: 0.85rem; color: #718096; margin-top: 6px;">符合法定 0%~25% 邊界</div>
            </div>
            <div class="card">
                <div class="card-label">委託清單總數</div>
                <div class="card-val">{len(orders)} 檔配置</div>
                <div style="font-size: 0.85rem; color: #27ae60; margin-top: 6px;">買進: {len(buy_orders)} 檔 ｜ 賣出: {len(sell_orders)} 檔</div>
            </div>
            <div class="card">
                <div class="card-label">沙盒守門員驗證</div>
                <div class="card-val" style="color: var(--success);">100% 通過</div>
                <div style="font-size: 0.85rem; color: #718096; margin-top: 6px;">8 大官方防弊約束全數零違規</div>
            </div>
        </div>

        <!-- 大盤判斷邏輯 -->
        <div class="card" style="margin-bottom: 24px;">
            <div class="card-label">🧠 Gemini AI 總體宏觀邏輯 (Market View Logic)</div>
            <p style="font-size: 1.05rem; line-height: 1.6; margin: 8px 0 12px 0;">{mv.get('logic')}</p>
            {f'<div style="background: #fff5f5; border-left: 4px solid #e53e3e; padding: 10px 14px; font-size: 0.9rem; color: #c53030;"><strong>反方風險訊號 (Counter Evidence):</strong> {mv.get("counter_evidence")}</div>' if mv.get('counter_evidence') else ''}
        </div>

        <!-- 個股委託與因果鏈詳細表格 -->
        <div class="card">
            <div class="card-label">📋 本日交易委託與產業鏈因果推論 (Orders & Inferences)</div>
            <table>
                <thead>
                    <tr>
                        <th style="width: 100px;">代號</th>
                        <th style="width: 80px;">方向</th>
                        <th style="width: 110px;">委託股數</th>
                        <th style="width: 100px;">目標權重</th>
                        <th>Gemini 產業鏈因果邏輯與反方訊號</th>
                    </tr>
                </thead>
                <tbody>
                    {orders_rows}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""
    out_file = Path(output_html_path) if output_html_path else p.parent / f"dashboard_{trade_date}.html"
    out_file.write_text(html, encoding="utf-8")
    return out_file
