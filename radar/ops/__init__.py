"""Operational helpers that are not part of the daily pipeline.

Kept out of `radar.notify` and `radar.pipeline` on purpose: a repair that
imports the crawler or the Telegram stack just to prune backups is a repair
that can fail closed for the wrong reason.
"""
