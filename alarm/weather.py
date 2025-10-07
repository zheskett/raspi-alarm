import asyncio

import python_weather
import python_weather.forecast


async def get_weather(
    location: tuple[float, float],
) -> python_weather.forecast.Forecast | None:
    async with python_weather.Client(unit=python_weather.IMPERIAL) as client:
        try:
            return await client.get(str(location))
        except:
            return None
