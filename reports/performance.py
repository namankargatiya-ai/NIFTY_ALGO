"""Computes the end-of-day performance report from a list of closed-trade rows."""
import config


def compute_eod_stats(rows, trading_date):
    total = len(rows)
    wins = [r for r in rows if (r["pnl_points"] or 0) > 0]
    losses = [r for r in rows if (r["pnl_points"] or 0) <= 0]
    target_hits = [r for r in rows if r["exit_reason"] in ("Target", "Trailing Stop")]
    sl_hits = [r for r in rows if r["exit_reason"] == "Stop Loss"]

    gross_profit = sum(r["pnl_rupees"] for r in wins) if wins else 0
    gross_loss = sum(r["pnl_rupees"] for r in losses) if losses else 0
    net = gross_profit + gross_loss

    avg_win = round(gross_profit / len(wins), 2) if wins else 0
    avg_loss = round(gross_loss / len(losses), 2) if losses else 0
    largest_win = max((r["pnl_rupees"] for r in wins), default=0)
    largest_loss = min((r["pnl_rupees"] for r in losses), default=0)
    profit_factor = round(gross_profit / abs(gross_loss), 2) if gross_loss != 0 else (
        "inf" if gross_profit > 0 else 0)
    total_points = sum(r["pnl_points"] for r in rows if r["pnl_points"] is not None)
    charges = total * config.ESTIMATED_CHARGES_PER_TRADE

    return {
        "Trading Date": str(trading_date),
        "Total Trades": total,
        "Winning Trades": len(wins),
        "Losing Trades": len(losses),
        "Win Rate (%)": round(100 * len(wins) / total, 2) if total else 0,
        "Target Hits": len(target_hits),
        "Stop Loss Hits": len(sl_hits),
        "Gross Profit (Rs)": round(gross_profit, 2),
        "Gross Loss (Rs)": round(gross_loss, 2),
        "Net Profit (Rs)": round(net, 2),
        "Total Lots Traded": total * config.POSITION_SIZE_LOTS,
        "Average Winning Trade (Rs)": avg_win,
        "Average Losing Trade (Rs)": avg_loss,
        "Largest Winning Trade (Rs)": largest_win,
        "Largest Losing Trade (Rs)": largest_loss,
        "Profit Factor": profit_factor,
        "Total Option Premium Points Captured": round(total_points, 2),
        "Total Brokerage & Charges (Rs, est.)": round(charges, 2),
        "Final Net P&L (Rs)": round(net - charges, 2),
    }
