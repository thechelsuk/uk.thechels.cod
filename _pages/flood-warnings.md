---

layout: page
title: Flood Warnings Feed
seo: flood warnings in Cheltenham
permalink: /flood-warnings

---

This project fetches flood warning data for the Gloucestershire area and publishes it as an [RSS feed](/flood.xml).

## Setup

1. Configure GitHub action runs on a schedule and choose your location
2. Action saves data as`data.json`
3. Python script executed in the action converts `flood.json` to `flood.xml`
4. Published using a GitHub Pages the RSS feed is available.

## Latest

<!-- flood_marker starts -->
- Flood alert: River Churn and its tributaries
- River levels are slowly falling on the River Churn. Flooding of low lying land and roads remains possible today, 07 January 2026,  in the Cerney Wick area. The weather forecast is for a mostly dry, windy day, with light rain this evening and overnight. Further rain is forecast for tomorrow. We expect river levels to remain high over the next few days.

We are closely monitoring the situation. Avoid using low lying footpaths and any bridges near local watercourses. Go to River levels online for updates on current river levels.

This message will be updated by 12:00, midday, on the 08 January 2026 or as the situation changes.

- Flood alert: River Windrush from Bourton to Newbridge
- River levels are slowly falling on the River Windrush. Flooding of low lying land and roads remains possible today, 06 January 2026, in the Newbridge area. The weather forecast is for a mixture of rain, sleet and snow today and overnight. We expect river levels to remain high over the coming days.

We are monitoring rainfall and river levels. Avoid low lying roads near rivers, which may be flooded. Go to River levels online for updates on current river levels.

This message will be updated by 12:00, midday, on the 07 January 2026 or as the situation changes.


<!-- flood_marker ends -->