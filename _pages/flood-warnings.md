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

<!-- flood_marker ends -->