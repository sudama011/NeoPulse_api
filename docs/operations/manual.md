# Telegram Command Manual

The Telegram bot is the primary interface for controlling NeoPulse.

| Command | Description | Example |
| :--- | :--- | :--- |
| `/start` | Wakes up the bot and checks connectivity. | `/start` |
| `/status` | System health check (API Latency, DB Connection). | `/status` |
| `/pnl` | Shows Realized and Unrealized P&L for the day. | `/pnl` |
| `/positions` | Lists all open positions with MTM. | `/positions` |
| `/kill` | **EMERGENCY:** Cancels all orders, exits positions, stops engine. | `/kill` |
| `/mode` | Toggles between PAPER and LIVE (Requires Admin Auth). | `/mode LIVE` |

## Alert Levels
*   **INFO (Silent):** Order Placed, Strategy Started.
*   **WARNING (Sound):** Order Rejected, High Latency (>500ms).
*   **CRITICAL (Persistent Ring):** API Disconnect, Stop Loss Hit, Margin Call.
