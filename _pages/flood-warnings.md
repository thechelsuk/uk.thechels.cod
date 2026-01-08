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
- River levels have fallen on the River Churn. However, flooding of low lying land and roads remains possible today, 08 January 2026, due to heavy rain which is forecast this afternoon and into Friday morning. We expect river levels to remain high over the next few days.

We are closely monitoring the situation. Avoid using low lying footpaths and any bridges near local watercourses. Go to River levels online for updates on current river levels.

This message will be updated by 12:00 PM, midday, on the 09 January 2026 or as the situation changes.

- Flood alert: River Windrush from Bourton to Newbridge
- River levels have fallen on the River Windrush. However, flooding of low lying land and roads remains possible today, 08 January 2026, due to heavy rain which is forecast this afternoon and into Friday morning. We expect river levels to remain high and responsive to further rainfall over the next few days.

We are monitoring rainfall and river levels. Avoid low lying roads near rivers, which may be flooded. Go to River levels online for updates on current river levels.

This message will be updated by 12:00 PM, midday, on the 09 January 2026 or as the situation changes.


<!-- flood_marker ends -->