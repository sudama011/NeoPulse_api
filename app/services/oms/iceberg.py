import asyncio
import logging
import math

from app.services.oms.executor import order_executor

logger = logging.getLogger("IcebergAlgo")


async def place_iceberg_order(symbol: str, token: str, side: str, total_qty: int, freeze_qty: int = 1800):
    """
    Slices a large order into smaller chunks to bypass exchange freeze limits.
    """
    if total_qty <= freeze_qty:
        # Send normal order
        return await order_executor.place_order(symbol, token, side, total_qty)

    num_legs = math.ceil(total_qty / freeze_qty)
    logger.info(f"🧊 ICEBERG: Slicing {total_qty} into {num_legs} legs of ~{freeze_qty}")

    remaining_qty = total_qty

    for i in range(num_legs):
        leg_qty = min(remaining_qty, freeze_qty)

        logger.info(f"🧊 Executing Leg {i+1}/{num_legs}: {leg_qty} qty")
        response = await order_executor.place_order(symbol, token, side, leg_qty)

        if not response or response.get("stat") != "Ok":
            logger.error(f"❌ Iceberg Leg {i+1} Failed! Stopping chain.")
            return {"status": "partial_failure", "filled": total_qty - remaining_qty}

        remaining_qty -= leg_qty

        # Optional: Random delay to hide footprint
        await asyncio.sleep(0.5)

    return {"status": "success", "total_filled": total_qty}
