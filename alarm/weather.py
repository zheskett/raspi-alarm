import asyncio

import python_weather
import python_weather.forecast


async def get_weather(
    location: tuple[float, float] | str,
) -> python_weather.forecast.Forecast | None:
    async with python_weather.Client(unit=python_weather.IMPERIAL) as client:
        try:
            weather = await client.get(str(location))
            return weather
        except Exception as e:
            return None
