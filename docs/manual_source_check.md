# Manual Source Check List

This list tracks companies and source families that are useful but are not active direct scrapes in the current bundled source inventory. Check these manually by opening the careers page, identifying the real public ATS surface, adding the slug or URL to `source_watchlist.json`, then using `Sources -> Probe Watchlist`.

Last updated: 2026-05-17.

## Watchlist Companies Not Auto-Enabled

Security:
- Aqua Security
- Arctic Wolf
- Check Point
- CyberArk
- Lacework
- Proofpoint
- Rapid7

Systems, chips, and infrastructure:
- AMD
- Arm
- Enfabrica
- Etched
- Fermyon
- Gitpod
- Groq
- HashiCorp
- Intel
- Lambda Labs
- Marvell
- Micron
- Oxide Computer
- Qualcomm
- Rivos
- Salesforce
- ScyllaDB
- SiFive
- Timescale

Cloud, devtools, and data:
- Aiven
- Atlassian
- Chronosphere
- Coralogix
- DigitalOcean
- Doist
- Fleet
- Fly.io
- Harness
- Momento
- Pulumi
- Qdrant
- Spacelift
- Upstash

India and Bangalore-focused companies:
- Amagi
- Ather Energy
- BrowserStack
- Cashfree
- Chargebee
- Flipkart
- Hasura
- Juspay
- Khatabook
- KreditBee
- MoEngage
- Niyo
- Ola Electric
- Pine Labs
- Razorpay
- ShareChat
- Swiggy
- Udaan
- Urban Company
- Whatfix
- Zepto
- Zerodha

Marketplace, mobility, and consumer infra:
- Automattic
- Bolt
- Cruise
- Gojek
- Rivian

## Disabled Rows That Need Manual Verification

High-value company rows:
- Fortinet: no stable direct public source is bundled; look for a stable public careers feed.
- Hudson River Trading: no stable direct public source is bundled; check for a Greenhouse/Lever/Workday replacement.
- Juniper Networks: no stable direct public source is bundled; check for a direct ATS/API replacement.
- Palo Alto Networks: no stable direct public source is bundled; check for a direct ATS/API replacement.
- Snyk: previous Greenhouse token returns 404; find the current public board.
- Two Sigma: no stable direct public source is bundled; check for a direct ATS/API replacement.
- Virsec: no dedicated careers/jobs feed found on the current public site.

Portal or marketplace rows:
- FlexJobs
- Jobs24x
- JustRemote
- Remote.co
- RemoteFront
- Underdog
- Wellfound
- YC Work at a Startup

Aggregator/search rows that should stay disabled until current markup is verified:
- AI Jobs
- Built In
- Climatebase
- CyberSecJobs
- DataJobs
- Devsnap
- Foundit
- GolangProjects
- Himalayas
- Hirist
- Instahyre
- Levels.fyi
- ML Jobs
- Naukri
- NoDesk
- Otta / Welcome to the Jungle
- Python.org Jobs
- Remote Rocketship
- Rust Jobs
- TimesJobs
- Working Nomads

## Manual Check Workflow

1. Open the company careers page in Firefox.
2. Inspect whether the page redirects to Greenhouse, Lever, Ashby, SmartRecruiters, Workday, iCIMS, Jobvite, Workable, Teamtailor, or BambooHR.
3. Add the discovered slug or API URL to `Documents\JobScraper\config\source_watchlist.json`.
4. Run `Sources -> Probe Watchlist`.
5. If the report validates title, URL, location, and job ID, the app promotes and imports the row automatically.
6. If the report stays blocked or incomplete, keep the source disabled and document whether it needs browser auth, an API key, or a custom adapter.
