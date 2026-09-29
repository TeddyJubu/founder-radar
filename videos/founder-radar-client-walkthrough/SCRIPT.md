# Founder Radar client walkthrough

## 00:00:00,000 — Your morning, in one place

Welcome to Founder Radar. This short walkthrough shows where the companies come from, how the fund matches are calculated, and how to change the settings. Your daily job starts on Today. You review companies, rather than a pile of articles. Kept holds the companies you want to revisit. The names and example facts are teaching data. The completed checks in this copy are simulated. We are not changing your live list, your decisions, or your Google Sheet.

## 00:00:25,028 — Two tracks. Different jobs.

Track A looks for useful signals. These can be university spinouts, accelerator companies, grant awards, or a specific technology news story. Track B uses Companies House, the official company register. It helps verify the company identity and incorporation date. A newly registered company is not automatically a promising startup. Registration is also not proof of when trading began, or proof that no investment has happened. Missing facts stay unknown until there is evidence.

## 00:00:52,923 — How a clue becomes a company card

Here is the whole journey. A source provides a clue. The system checks which company it belongs to, combines duplicate sightings, and keeps the supporting links. It turns longer text into small facts, such as location, business type, stage, and funding evidence. Next come the fund rules and the scores. This calculation uses code, not a chat model. The same facts and settings give the same result. Finally, Hermes checks the proposed company before publication. This last check can withhold a bad match. It cannot invent another company or change the calculated scores.

## 00:01:25,332 — Read Today without guessing

On Today, start with the company name and the short description. Check where it is based, the evidence about its age, and the suggested fund. Match means how closely the known facts fit that fund's preferences. Fresh means the discovery opportunity, using the evidence available about age, tracked coverage, funding, and how it was found. A high Fresh score is not proof that nobody knows the company. Coverage tells you how much evidence supports the match. If a field says unknown, treat it as a gap, rather than silently filling it in.

## 00:01:54,018 — Four funds. Keep the real rules.

The four fund groups are Northstar, DSW, Outward, and Anticus. Northstar has regional and specialist vehicles, with the North East especially relevant. DSW has separate SEIS and EIS paths. Outward looks at early technology serving complex industries. Anticus has Yorkshire regional vehicles. London remains eligible for the softer DSW EIS regional rule. When another suitable fund matches, Today can suggest that alternative and explain the regional preference. If none does, it shows the weaker regional match honestly.

## 00:02:25,108 — Understand the calculation

Let us use one fictional company, River Finance. Our example says it is a twelve month old UK fintech company, with pilot customers and two million pounds of disclosed funding. The current default calculation gives Outward a Match of sixty one point seven, and Fresh of sixty five point five. Priority combines these: sixty percent Match, plus forty percent Fresh. That gives sixty three point two after rounding. These numbers describe the example under these settings. Hard rules decide eligibility first. An attractive score cannot rescue a company that fails an applicable hard rule.

## 00:02:58,562 — Change a source safely

The Google Sheet is the durable place for configuration: sources, fund criteria, scoring weights, and general settings. Here we use a local replica, clearly marked demo. In Sources, we switch northern accelerator from enabled to disabled, then save the demo settings. This really updates the source configuration. A future collection run will skip that source. It does not erase companies already stored, and it does not automatically lower their scores. For the live system, make a small deliberate edit, check that configuration loads, and then run the appropriate update flow.

## 00:03:31,010 — Change a genuine fund rule

Now we edit a real field from the default Outward fund row: prior total max, the ceiling used for previously raised capital. The default row contains twenty million pounds. For teaching only, we tighten it to one million. This is not a claim that Outward changed its mandate. River Finance has two million of previous funding in our example, so it now fails this vehicle's hard rule. Rescoring returns reject, with no eligible vehicle. In real use, only change a fund rule when the fund's current criteria support that change, and keep the evidence for it.

## 00:04:02,279 — Thresholds and weights do different things

The shortlist threshold controls the label. Our default Match threshold is seventy. Lowering it to sixty puts River Finance on the shortlist because the example also meets the Fresh and coverage conditions. Match stays sixty one point seven. Fresh stays sixty five point five. We changed the threshold, not the facts or their scores. Next we restore the threshold and change Priority to eighty percent Match and twenty percent Fresh. Priority becomes sixty two point five. The two underlying scores still stay the same. Weights control emphasis. Thresholds control which bucket a result enters.

## 00:04:36,212 — Save → rescore → final check → publish

After a live settings change, load the updated configuration and rescore the stored companies. The system records which configuration produced each score, so old results can be identified. Hermes must then complete the final company checks for the updated candidates. A successful calculation alone does not approve publication. If the final check is unavailable or incomplete, the company waits. It stays off Today, the published Today sheet, and the digest until it passes. This is useful protection: a quiet morning is better than presenting an unchecked list as ready.

## 00:05:07,978 — Ready and waiting are separate

The counter separates ready to review from awaiting final check. In our demo, two companies are ready and one is waiting. The ready count reflects the remaining review backlog, rather than resetting to the number on one page. Today also aims for a useful mix of qualified sources, funds, and sectors. This is soft ordering, not a compulsory equal quota. A company does not get fake points to make the mix look balanced. Quality and the real fund rules still come first.

## 00:05:34,155 — Keep your decisions in the same store

Three buttons finish the morning review. Worth contacting saves the company to Kept. Unsure also saves it there for later. Not for me stores the rejection, and that company does not return to Today. Here we actually save one decision in the isolated copy, then open Kept. The application database, called SQLite, is the main store for these choices. The Sheet can mirror the verdicts when configured, and a full synchronization catches up missed writes. Phone actions must call the same saved decision command. A chat reply alone is not a saved choice.

## 00:06:05,830 — A simple daily routine

For daily use, open Today, check the explanation and evidence, then choose yes, maybe, or no. Return to Kept when you are ready to contact companies. Change settings in the Sheet when you have a clear reason, then recalculate and complete the final checks before publishing again. Use the teaching guide for the Easy explanation or the Technical detail. This walkthrough used demonstration data throughout. Your live settings and decisions were untouched. The goal is a small, explainable UK company list that helps you make the next decision, while leaving unknowns and unfinished checks visible.

## 00:06:37,966 — Your control room and logins

Now let us make you the owner of the day to day operation. Bookmark the review website, the shared Google Sheet, and the Hermes Web UI address shown here. Today, Kept, Dashboard, and Help use the review login. The teaching guide is public. Hermex connects to the webui address, using its app password, without custom headers. The similar hermes address is a review page alias, not the chat service. Use the Sheet link supplied with your handover. Passwords, your server key, and account invitations must be supplied privately. This video deliberately does not display them.

## 00:07:09,412 — Ask Hermes for an action you can verify

You can speak to Hermes through Telegram or Hermex. Be specific about the outcome. Say: run a fresh Founder Radar scan, complete the final checks, and give me the Today link. For research, ask it to run a new UK company search and give you the Today link. To keep or reject a company, name it precisely and ask Hermes to save the decision with founder radar decide. Then confirm the saved result. For sources or fund rules, Hermes can explain the row and proposed change. Edit the accepted cell yourself in Google Sheet, then ask Hermes to rescore and complete the final checks. A brand new source website needs a developer to add a tested connector.

## 00:07:45,663 — Your daily and weekly checklist

Each morning, check Today, the ready and waiting counts, and your saved choices. If the list is empty, ask why rather than assuming the scan failed. Once a week, ask Hermes for a health summary: the most recent successful scan, source failures, Sheet synchronization, available disk, and the newest backup. Ask it to show dates, not just say everything is fine. Once a month, review fund criteria, recurring costs, and backup access. A source that returns nothing for weeks can have a broken page layout, even when it reports success. Your routine is to notice that pattern and ask Hermes to investigate it.

## 00:08:19,049 — Three useful checks, in plain language

Doctor means the system health check. Why today means an explanation of the candidate counts and the reasons companies are excluded. These are good first diagnostic steps. Ask Hermes to run them on the actual server and explain failures in plain English. Today QA is different: it performs and saves the final company checks, and may withhold a bad card. The limit eighty example checks a larger pending batch; completed current approvals do not use those new check slots. If a check cannot finish, keep the company waiting. If chat works but final checks repeatedly fail, ask Hermes to check shared access, use the installed repair, then recheck. Do not solve an empty list by switching off the protection or forcing an unchecked digest to send.

## 00:09:01,201 — What runs on the VPS

The VPS is the computer that stays on when your laptop is closed. A service is one running job. The Founder Radar web service serves the review app. Caddy handles its public secure address. The Hermes gateway handles conversations, and the Hermes Web UI supports Hermex. Hermes runs as aryan; the company database work runs as radar. Use the supported founder radar command wrapper, which switches to the correct account. Do not fix a permission problem by making everything writable or changing ownership of the application. To check the machine, ask Hermes to inspect failed services, timers, free disk, and recent error logs without printing secrets. Administration needs an owner account with verified permission; the command wrapper does not grant general administrator access.

## 00:09:45,714 — Updates, schedules and sensible reboots

The scheduled jobs use London time. Database backup is at five, the scan starts around six thirty with a small delay, and the heartbeat checks for a missed morning at nine. The trusted project updater checks approved main branch changes about every five minutes. The server also has automatic operating system security updates enabled. These are different update paths. For a Hermes upgrade, ask it to check the installed runtime and compatibility first, back up configuration, and test conversations and final QA afterwards. Do not paste a random reinstall command. Reboot only when a real maintenance reason requires it, at a quiet time, after reviewing the plan. Automatic reboot is not guaranteed.

## 00:10:26,539 — Backups protect choices. Recovery needs a plan.

The daily database snapshots are in the folder shown here, with fourteen days retained. Ask Hermes to confirm the newest file and test that it is readable. This protects stored companies and choices, but a local database backup alone does not protect against losing the entire server. Keep an encrypted complete recovery copy in storage you control, including private configuration and Sheet rules. That off-server copy has not been confirmed by this handover. Before restoring, ask for the exact backup, the choices that would be lost, and a recovery plan. Stop writers, preserve the current state, and use the validated restore command. Confirm a destructive recovery before it happens.

## 00:11:04,533 — When Today, Telegram or Hermex stops

If Today will not open, check its address and review login, then ask about the web service and Caddy. If Today opens but is empty, use doctor and why today; an unfinished company check is not a website outage. If Telegram is silent, try a simple message, check the gateway, and check whether the latest scan and publication gate completed. Use Hermex if that channel still works. If Hermex fails, verify its webui address and app password, try the same address in a browser, and check the Web UI service. If all conversation channels are down, use your VPS console or authorized SSH access. Diagnose, repair the specific service, then test the original channel again.

## 00:11:44,046 — When the Google Sheet is behind

If the Sheet is behind but Kept contains your choices, do not delete or re-enter everything. The app database is the main saved record. Ask Hermes to run doctor, confirm that the Google service account, the account the server uses, still has Editor permission to change the Sheet, and inspect the latest synchronization. Then request sync sheet and verify the intended rows; this action writes the Sheet mirror. For changed rules, check the Status cells. Invalid settings can fall back to the last good copy, so a saved cell is not proof that the new rule loaded. Keep the fixed fund and vehicle keys unchanged. After a valid rule edit, rescore and complete the final checks again. Before synchronizing a restored database, preserve and review the Sheet inputs, because newer Sheet verdicts can change restored choices.

## 00:12:29,613 — Own the accounts and protect the keys

Make sure you control the VPS account, the Sheet and Google Cloud project, the GitHub repository, the Telegram bot, and the AI provider actually used by Hermes. Obtain the needed invitations and recovery information; this video does not mean ownership was already transferred. Keep passwords and private keys in your password manager, with suitable recovery contacts and two factor authentication. Review real billing and renewal dates. When rotating a key, prepare the replacement privately, update the correct protected configuration, test the affected connection, and only then revoke the old key. Never ask Hermes to paste credentials into chat, logs, screenshots, or a public guide. Hermes's final-check login and the extraction AI credential are separate connections, so test the one you actually changed.

## 00:13:15,986 — Change passwords without losing access

For the review password, an administrator runs Caddy hash password interactively. Store the generated hash in the protected Radar configuration, restart Caddy, and test the new review login. Do not type a real password into a copied command that may remain in history. Hermex uses a different app password. Identify the installed Web UI configuration location without printing its contents, replace that password privately, restart the Web UI service, and update your phone. For Telegram, renew the token through BotFather and update every consumer using it. For an expired AI login, check the actual working Hermes installation before using its supported setup. Test the changed connection before removing the old access.

## 00:13:57,293 — You can run the next day yourself

Here are two prompts you can reuse. For health: check Founder Radar on the VPS, diagnose first, and show the latest scan, ready and waiting counts, failed services, disk, newest backup, and Sheet access, without printing secrets. For a repair: explain the cause and the smallest fix, take a backup where needed, ask before restoring, deleting, rebooting, or rotating keys, then test the original problem. If chats are down, use your provider console or your own authorized SSH connection. The address is shown here; a private local shortcut on someone else's laptop is not your access setup. Keep the written owner handover alongside this video. You now have a daily routine and a recovery route.
