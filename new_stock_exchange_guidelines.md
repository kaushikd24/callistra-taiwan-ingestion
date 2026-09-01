# PROJECT OVERVIEW

This is Callistra, a financial markets research platform, where we ingest stock exchange filings, financial filings, company IR material, regulatory documents, clinical trials, etc. and made the entire corpus retrievable for end users and AI Agents. The product is an enterprise SaaS, all systems are proprietary.

# GUIDELINES FOR ADDING A NEW STOCK EXCHANGE

** REFERENCE FOR HUMANS AND AI AGENTS **

## CORE PRINCIPLES

- "Adding" a new stock exchange implies ingestion that specific stock exchange's corporate filings such as but not limited to: Press Release, Corporate Announcement, Mergers/Acquisitions, Business Updates, Financial Results etc.

- What MUST be added: general everyday disclosures

- Bonus: Glossy investor relations material such as investor presentations, annual reports, slide decks etc. -- these are generally NOT available from all stock exchanges, but if they are available, they should be ingested before asking permission from the user, as multiple pipelines already ingest "glossy IR material".

- Certain stock exchanges may require translation of their filings into English.

- Always begin with having a csv/list of companies listed in the stock exchange, usually this list is: %%company_mapping_with_gics.csv%% or similar names added by the user in the root directory, this CSV would help mapping a company's symbol (eg: NYSE NDVA or LSE HSBA or NSE RELIANCE or XETRA BAYN) with its ISIN, country (human friendly such as USA/GERMANY/UK), country-code iso alpha-2, exchange, full company name (NVIDIA corporation) and more.

- Whenever the user would say "news" it would imply stock exchange filings/regulatory filings/corporate announcements/press releases and other equivalents and NOT WSJ/Bloomberg/RSS feed style news articles.

## TECHNICALITIES

### HOW TO BEGIN TESTING THE INGESTION PIPELINE

- The user (the human developer) would start with exploring the "website" of the stock exchange to identify the primary url where the news is consolidated.

- The user would identify a networking call and paste the CURL which would "likely" fetch the json/equivalent of the news' data. the user might also paste a .har file from the network calls for the AGENT to inspect.

- The curl needs to be run by the AGENT to check whether it needs authentication/browser cookies etc., if the stock exchange involves workaround-able bot protection, the AGENT MUST try once with curl_cffi impersonate "chrome", and if the AGENT still encounters further bot protection such as but not limited to AWS style WAF/JS challenges, PerimeterX bot protection or cloudflare cf_clearance, then the AGENT MUST STOP AND POINT THIS OUT TO THE USER BEFORE TAKING FURTHER ACTION.

- The AGENT must robustly ANALYSE the API of the stock exchange and reports key findings such as: how does the exchange behave under "load", repeated API calls, the "level" of bot-protection it has, on a scale of: 1. minimal: easily bypass-abel, 2: needs curl_cffi, 3: AWS style JS challenges. 

- Current situation: SEC API: only user agent, LSE RNS: only user agent with appropriate headers, INDIA NSE/BSE: no bot protection, Deutsche Borse: only x-security, Euronext: complete AWS WAF/JS challenge based gating

- The AGENT must also attempt to "explore" the website through a sitemap "if available" and report findings to the user.

- These are the "tests" which must pass before "productionising": 1. The AGENT/User have sufficient confidence in their pipeline and they believe it could be automated to run "indefinitely", 2.The AGENT/User have not missed any "obvious" API which "was available" (see below) but could not be ingested due to some difficulties, 3. The AGENT/User have atleast figured out the daily news ingestion pipeline.

### HOW TO DE-DUPE/INGEST/STORE 

- The AGENT/USER after their "tests" pass have to come up with a SQL table(s) for their stock exchanges filings. The AGENT/USER must have thoroughly read `new_ingestion_guidelines.md` to understand the ingestion flow. 

- Document storage, the `blob_path` has to be AWS S3, the but the exact bucket would be decided by the USER.

- In certain cases, the "news" would directly be `machine readable` in the sense that it is plain text/json/html which we can store and we would NOT require ML based OCR. Eg case: LSEG

- In other cases, the "news" would point to a document, or would require the pipeline to OCR the filing downstream. Eg case: NSE/BSE.


- In some cases, "translation" would be required of the material. Eg: certain Euronext filings. The exact translation mechanism is yet to be engineered in the downstream pipelines and would be mentioned in the `new_ingestion_guidelines.md`, in case it is NOT mentioned, assume that translation is unavailable "yet" and be sure to point this out to the USER. the USER can rule-out translation but the AGENT must WARN the USER that the particular stock exchange NEEDS some translation. The product policy is to not store documents without an ENGLISH version.

- In all cases, the "document" is embedded by the embedding generation pipeline.

- Each document has its own, unique uuid.

- De-dupe is to be done by either: url of the filing, (primary_symbol, datetime-ISO), or something else unique to the "document".

- The USER may/may not mention if a company is listed under various exchanges. In those cases, the de-dupe would happen "downstream" and would happen before OCR. the functionality for such a de-dupe is not yet ready. Eg: NVDA is listed across: NASDAQ, LSE, XETRA etc. -- currently the pipeline does not de-dupe between the filings, but it would, likely using ISIN. In most cases, the stock ingestion pipeline MUST INGEST ALL COMPANIES' AND SHOULD NOT ATTEMPT TO DE-DUPE CROSS EXCHANGE FILINGS.


- THE USER/AGENT MUST EXPLORE THE EXCHANGE'S INTERNAL CLASSIFICATION CRITIREA for "news" such as "Acquisition" or "Annual Report" or in case of SEC 10K or 8K, and the USER/AGENT must attempt to form a mapping between the stock exchange's news classification, and the product's news classification (see below/see `new_ingestion_guidelines.md`) as canonical_doc_type.

- A common case: the stock exchange has 100+ different classification (eg: 400+ in case of NSE/BSE, 100+ in case of LSEG), in such a case the AGENT/USER MUST spawn a GPT loop and prompt it to classify the news classification into the product's taxonomy through a prompt. NOTE that this GPT loop based classification has to be performed OFFLINE, and has to be performed once, locally. It is not like a runtime api which would occur everytime a new, unknown document is claimed.

- How to "know" which all stock exchange based news classifications exist: fetch previous news articles for atleast 6months-1yr, and extract the news classifications, and prepare them for GPT.

#### Below are the product suppported classifications

Every ingested document **must** be mapped to one of Callistra's canonical document types. These values are used throughout search, analytics, filtering, document grouping, and downstream AI pipelines.

Do **not** invent new values unless the user explicitly requests a taxonomy change. If a document cannot be confidently mapped, use `other` and mention the ambiguity to the user.

Current supported canonical document types:

| Canonical Document Type | Typical Examples |
|--------------------------|------------------|
| `annual_report` | Annual reports, 10-K, 20-F, yearly reports |
| `financial_results` | Quarterly results, interim reports, earnings releases, financial statements |
| `earnings_transcript` | Earnings call transcripts |
| `earnings_call_update` | Earnings call announcements, webcast notices, analyst call invitations |
| `investor_presentation` | Investor presentations, slide decks, capital markets day presentations |
| `press_release` | Corporate press releases |
| `business_updates` | Business updates, operational updates, company developments |
| `general_disclosure` | General regulatory disclosures, 6-K, 8-K, miscellaneous exchange announcements |
| `management_change` | CEO/CFO appointments, resignations, executive management changes |
| `board_meeting` | Board meeting notices and outcomes |
| `shareholder_meeting` | AGM, EGM, shareholder meeting notices |
| `shareholder_communication` | Letters to shareholders and shareholder communications |
| `corporate_action` | Dividends, stock splits, bonus issues, buybacks, rights issues |
| `fundraising` | Equity raises, debt issuance, private placements, convertible securities |
| `mna_restructuring` | Acquisitions, mergers, divestitures, restructurings, spin-offs |
| `litigation_regulatory_action` | Litigation, enforcement actions, regulatory proceedings |
| `regulatory_compliance` | Compliance filings, governance reports, statutory disclosures |
| `regulatory_general` | Used for non-company specific regulatory sources such as but not limited to: "ClinicalTrials.gov", "FDA.gov", "PBI.gov" etc.
| `other` | Documents that cannot be confidently classified into any of the above |


## WHAT TO INGEST (APIS AVAILABLE)

- Initially, ENSURE that news can be reliably ingested on an hourly loop through a python based worker (and not a cloud cron) and de-dupe works (very important), and do not move to production without sidecar tables on sql (see below).

- After news is ingested, discuss with the USER about any potential EVENTS CALENDAR/ INVESTOR EVENTS of companies LSITED ON THE EXCHANGE, AND NOT THE EXCHANGE'S IR PAGE.

- If such an events calendar is available, mention this to the user/or the user might explore an api and tell the agent about an events calendar's availability. The event calendar can either be on the company inside the exchange's profile page (eg: Deutsche Borse) or it could be an exchange-wide events calendar (usually the case with some NASDAQs such as US, Baltics), or be not available at all.

- The events calendar "might" display upcoming earnings/events/agm/egm/etc.

- If an events calendar is available, the AGENT must ASK the USER about any SQL tables existing for events ingestion and if any sidecar table is required for that particular stock exchange's events ingestion.

- IPO documents: This is the "third" layer: Once a decision has been made on news/events, the AGENT/USER must look for ANY IPO/New Issue/Issuer related documents and ingest prospectuses, drhp style filings/etc.

- AN IPO SQL TABLE ALREADY EXISTS, AND THE AGENT MUST REQUEST THE USER FOR THE SCHEMA OF THE IPO TABLE (currently not mentioned in the new_ingestion_guidelines.md)

- The USER/AGENT MUST EXPLORE if XBRL/i-XBRL/HTML style financial filings are available from the stock exchange, IF NOT FOUND, THEY MUST CONTINUE WITH DOCUMENT INGESTION ONLY.

- The USER/AGENT are NOT required to ingest generic financial statements from a company's profile page on the stock exchange, IF AND ONLY IF xbrl style financials are available -- ingest ELSE continue with plain document ingestion.

## MOVING TO PRODUCTION

### STORAGE

- All storage is AWS S3, without confusion. the bucket name would start by callistra-* and the key style can be approximately callistra-*/{ticker}/{filing_name}-uuid.pdf/html

- IF THE STOCK EXCHANGE HAS AN HTML, THE AGENT MUST DUPLICATE THE HTML IN THE BLOB STORE, SO THAT THE PRODUCT FRONTEND CAN DIRECTLY RENDER THE HTML, AND IT WOULD STORE THE EMBEDDINGS-READY MACHINE READABLE MARKDOWN SEPARATELY -- THIS IS VERY IMPORTANT AND MENTION THIS TO THE USER BEFORE CONTINUING.

### PRODUCTION DATABASE

- THE BACKEND RELIES ON A MONOLITHIC GCLOUD DATABASE.

- How to connect to sql: the USER will provide for a module: analytics_db/db.py which has dependencies: pg[8000] || cloud-sql-python-connector. 

- the analytics_db module MUST NOT BE EDITED UNLESS EXPLICITLY REQUIRED/OR THE PIPELINE CANNOT FUNCTION WITHOUT A CHANGE.

- UNDER NO SCENARIO MUST THE ANALYTICS_DB BE REFACTORED. ONLY MINIMAL EDITS ARE REQUIRED.

- the analytics_db is used for sql read/writes.

- DO NOT RUSH TO MOVE TO PRODUCTION FROM DAY-1, ONLY DISCUSS ABOUT PRODUCTION ONCE THE PIPELINE FEELS RELIABLE FOR RUNNING.

### PRODUCTION VM

- The production VM can either be a `railway` instance in which case we would need a start.sh and a railpack.json (the user would usually provide these two files from another repository and the agent would change them to the pwd's specific needs), be sure to add a requirements.txt

- Or, the production VM can be any of gcloud/azure/aws style instances.

- When the project would move to production, the user would themselves mention the VM instance.

- The VM instance should not be reachable over the internet apart from development purposes.

## GUIDELINES FOR AI AGENTS

- The AGENT working on the codebase MUST MAINTAIN their own AGENTS.md or if the agent is a CLAUDE, they must maintain a CLAUDE.md with sufficient context of the codebase.

- The AGENT CAN ADDITIONALLY MAINTAIN A PROGRESS.MD (STRONGLY RECOMMENDED) OR A MEMORY.md

- THE AGENT IS REQUIRED TO COMPREHENSIVELY DOCUMENT THE CODEBASE WHEN THE CODEBASE IS IN PRODUCTION, before/in between production/development, no such codebase documentation is required.

- Based on the stock exchange, the agent might be required to explain financial/corporate action related terminologies, help with a language barrier, etc.

## GENERAL GUIDELINES

- Always have a specific, large cap to "test" for documents/finanicals/events:

USA             JPMorgan / NVIDIA 
UK              HSBC / AstraZeneca
INDIA           Reliance Industries

FRANCE          LVMH
GERMANY         Bayer
NETHERLANDS     ASML
SWITZERLAND     Roche
SPAIN           Santander
NORWAY          Equinor
SWEDEN          Volvo AB
FINLAND         Nokia
DENMARK         Novo Nordisk
ITALY           Eni

BELGIUM         AB InBev
IRELAND         AIB
PORTUGAL        EDP
AUSTRIA         OMV
POLAND          ORLEN
GREECE          National Bank of Greece
CZECHIA         CEZ
HUNGARY         MOL
ROMANIA         OMV Petrom
TURKEY          Koc Holding

HONG_KONG       Tencent
JAPAN           Toyota
CHINA            ICBC
SOUTH_KOREA     Samsung Electronics
TAIWAN          TSMC
SINGAPORE       DBS
AUSTRALIA       BHP

| Country          | Exchange              | Company I'd use     | Symbol | Why it's a good test                                                     |
| ---------------- | --------------------- | ------------------- | ------ | ------------------------------------------------------------------------ |
| 🇫🇷 France      | Euronext Paris        | **LVMH**            | MC     | Huge issuer, excellent IR, results/presentations, lots of disclosures    |
| 🇩🇪 Germany     | Xetra / Frankfurt     | **Bayer** ✓         | BAYN   | Keep this — very document-heavy                                          |
| 🇳🇱 Netherlands | Euronext Amsterdam    | **ASML**            | ASML   | Obvious choice; major global issuer, excellent financial documentation   |
| 🇨🇭 Switzerland | SIX                   | **Roche** ✓         | ROG    | Keep this — very good disclosure set                                     |
| 🇪🇸 Spain       | BME / Bolsa de Madrid | **Banco Santander** | SAN    | Better ingestion test than many industrials because banks publish *tons* |
| 🇳🇴 Norway      | Euronext Oslo         | **Equinor**         | EQNR   | Excellent reporting, presentations and exchange announcements            |
| 🇸🇪 Sweden      | Nasdaq Stockholm      | **Volvo AB** ✓      | VOLV B | Yep. Excellent choice; don't confuse it with Volvo Cars                  |
| 🇫🇮 Finland     | Nasdaq Helsinki       | **Nokia**           | NOKIA  | Probably the cleanest Finnish test company                               |
| 🇩🇰 Denmark     | Nasdaq Copenhagen     | **Novo Nordisk**    | NOVO B | I'd make Novo the canonical Danish test despite your other options       |
| 🇮🇹 Italy       | Euronext Milan        | **Eni**             | ENI    | Very disclosure-heavy; excellent financial/reporting material            |

| Country             | Exchange                  | Test company                | Symbol | Comment                                                                    |
| ------------------- | ------------------------- | --------------------------- | ------ | -------------------------------------------------------------------------- |
| 🇧🇪 Belgium        | Euronext Brussels         | **Anheuser-Busch InBev**    | ABI    | Large multinational, lots of reporting                                     |
| 🇮🇪 Ireland        | Euronext Dublin           | **AIB Group**               | A5G    | Good native Irish listed-company test                                      |
| 🇵🇹 Portugal       | Euronext Lisbon           | **EDP**                     | EDP    | Large, active issuer                                                       |
| 🇦🇹 Austria        | Vienna Stock Exchange     | **OMV**                     | OMV    | Great disclosure-heavy industrial                                          |
| 🇵🇱 Poland         | Warsaw Stock Exchange     | **PKN ORLEN**               | ORLEN  | One of the most important CEE companies                                    |
| 🇬🇷 Greece         | Athens Exchange           | **National Bank of Greece** | ETE    | Banks give you a rich document set                                         |
| 🇨🇿 Czech Republic | Prague Stock Exchange     | **ČEZ**                     | CEZ    | Large utility; lots of regulatory/financial material                       |
| 🇭🇺 Hungary        | Budapest Stock Exchange   | **MOL**                     | MOL    | Major regional oil & gas company                                           |
| 🇷🇴 Romania        | Bucharest Stock Exchange  | **OMV Petrom**              | SNP    | Large local issuer                                                         |
| 🇹🇷 Türkiye        | Borsa Istanbul            | **Koç Holding**             | KCHOL  | Fantastic conglomerate test case                                           |
| 🇮🇸 Iceland        | Nasdaq Iceland            | **Marel**                   | MAREL* | Historically useful Nasdaq Iceland test case; listing situation needs care |
| 🇱🇺 Luxembourg     | Luxembourg Stock Exchange | **ArcelorMittal**           | MT     | Interesting cross-listing test case                                        |


| Country        | Exchange       | Test company      |
| -------------- | -------------- | ----------------- |
| 🇪🇪 Estonia   | Nasdaq Tallinn | **Tallink Grupp** |
| 🇱🇻 Latvia    | Nasdaq Riga    | **DelfinGroup**   |
| 🇱🇹 Lithuania | Nasdaq Vilnius | **Ignitis Grupė** |

| Market              | Exchange                | Test company        | Symbol | Why                                                      |
| ------------------- | ----------------------- | ------------------- | ------ | -------------------------------------------------------- |
| 🇭🇰 Hong Kong      | HKEX                    | **Tencent**         | 0700   | Massive, highly followed, excellent reporting            |
| 🇯🇵 Japan          | Tokyo Stock Exchange    | **Toyota Motor**    | 7203   | Basically the Reliance-equivalent test company for Japan |
| 🇨🇳 Mainland China | Shanghai Stock Exchange | **Kweichow Moutai** | 600519 | Huge domestic A-share issuer                             |

| Country          | Exchange              | Test company                   |
| ---------------- | --------------------- | ------------------------------ |
| 🇰🇷 South Korea | Korea Exchange        | **Samsung Electronics**        |
| 🇹🇼 Taiwan      | Taiwan Stock Exchange | **TSMC**                       |
| 🇸🇬 Singapore   | SGX                   | **DBS Group**                  |
| 🇦🇺 Australia   | ASX                   | **BHP**                        |
| 🇳🇿 New Zealand | NZX                   | **Fisher & Paykel Healthcare** |
| 🇮🇩 Indonesia   | IDX                   | **Bank Central Asia**          |
| 🇲🇾 Malaysia    | Bursa Malaysia        | **Maybank**                    |
| 🇹🇭 Thailand    | SET                   | **PTT**                        |

There are 7 Nasdaq European exchanges:

#	Exchange	Country	MIC
1	Nasdaq Copenhagen	🇩🇰 Denmark	XCSE
2	Nasdaq Iceland (Reykjavík)	🇮🇸 Iceland	XICE
3	Nasdaq Tallinn	🇪🇪 Estonia	XTAL
4	Nasdaq Helsinki	🇫🇮 Finland	XHEL
5	Nasdaq Stockholm	🇸🇪 Sweden	XSTO
6	Nasdaq Riga	🇱🇻 Latvia	XRIS
7	Nasdaq Vilnius	🇱🇹 Lithuania	XLIT

They're conventionally split:

Nasdaq Nordic: Copenhagen, Stockholm, Helsinki, Iceland.

Nasdaq Baltic: Tallinn, Riga, Vilnius.

- About data backfills: usually not needed unless the user explicitly asks/requires.

- All backfill if any must be done by checkpointing in a CSV file, which would later be \copy to the postgres.

- DO NOT ATTEMPT TO ATTACH SCRAPERS TO ANY OTHER WEBSITE INCLUDING BUT NOT LIMITED TO: alphasense, tradingview, yahoo finance, morningstar, bloomberg, stock analysis dot com, financial filings dot com

- DO NOT ATTEMPT TO ATTACH SCRAPERS TO ANY OTHER STOCK EXCHANGE APART FROM WHATEVER THE USER/AGENT AGREE ON THE INITIAL SCOPE, eg: do not add LSEG in a Deutsche Borse repo.

# BACKILL GUIDELINES

"Backfill" means fetching old documents and bringing it into Callistra's systems. While adding a new stock exchange, assume that by default we do not need to backfill.

A live filings poller would usually automatically backfill documents prior to the current date, and to avoid this behaviour, we need to keep a CUTOFF_DATE, meaning the live poller should not automatically poll for documents before this cutoff date, and this date should be the Monday of the current week. 

A backfill is NOT REQUIRED unless the user explicitly asks for it. If any backfills are required, they would be done "offline", meaning from the local user's system and not from a production deployment worker. 

## IF A BACKFILL IS REQUESTED

IF a backfill is required, the AI Agent MUST create CSVs for checkpointing documents, and these CSVs should exactly mimic the master documents table, and whichever sidecar/support tables the system uses, so that the user can easily postgres \copy the CSV.

A backfill happens in three steps:
Step-1: Documents are either located, or located+fetched directly. If they are fetched, they must be stored in AWS S3 by default in the systems' alloted bucket, usually callistra-[region or stock exchange name]-documents/ or something.

Step-2: In case OCR is required and the documents are PDF, the user would take the initial documents csv generated by the backfill fetch, and proceed with OCR. Callistra offers two types of OCR: A proprietary ML based OCR stack with sophisticated table/chart detection or a "fastocr" stack for speed. In case an OCR is NOT REQUIRED, which is the case when the source_system itself is a machine-friendly system, such as Korea's DART, SEC, LSEG's RNS and so forth, then IT IS THE RESPONSIBILITY OF THE REPOSITORY TO PERFORM THE OCR, and OCR here strictly means conversion from raw .xml/.xbrl/any machine readable source not a pdf to an embeddings friendly object.

Step-3: Embeddings: When the OCR process is complete, the documents csv would go to the callistra-batch-embed, where they are embedded in batches.

Final step: the postgres \copy, in which the ingestion status is set as per the guidelines to avoid any re-ocr or re-embed of the documents. 

IMPORTANT: no backfill step ever touches any cloud based virtual machines (unless a gpu based ML ocr is required, in which case the user decides the process automatically), everything happens locally on users machine.

# TESTING AND TROUBLESHOOTING

## TESTING

Testing is relatively extremely simple:
Before deployment, an end-to-end test is required for the following loop:

Step-1: Document fetch and classification into canonical_doc_type from the source (exchange's website, an api, something else)

Stpe-2: Database write and S3 upload

Optional Step-2b: If Required, then OCR in case of machine friendly sources.

Step-3: verifying the document was uploaded and records exist.

NOTE: you do not need to wait for translation/ocr runner based ocr/embeddings to be processed before concluding the test succeeds, the testing is limited to ingestion and if needed machine-friendly ocr.

## TROUBLESHOOTING

Some commonly seen issues:

- I was using a html/js based scraper-poller and it keeps breaking: This can happen because the scraper-poller relied on an html/js, do not use html/js based poller, always find an API underneath.

- My API poller keeps hitting errors: figure out the error, decrease request limits or spread requests across a larger time. If the API requires an API key, consider getting a new API key.

- Incomplete Bytes Read: This is a known issue in some INDIAN websites, notably the SEBI website, where the only technique to fetch a document is to read bytes from the server. The Server itself can often timeout leading to an "incomplete bytes read" issue, but this is usually automatically resolved by retries.

- Document/URL does not exist: This is another known issue, particularly happening with the BSE (Bombay Stock Exchange, India) website and the LSEG RNS system when you proceed with an LSEG backfill (~2yrs of documents are available), where the server might show a valid document url (usually hosted on some CDn style server), but fetching the document can return a 404. This has been observed and documented as "the server can arbitrarily archive the document (in case of BSE)" and "the server archives documents who have aged more than 2yrs from time.now()"

- Consent/Declaration needed: This is usually not an error, but is a "What is the purpose of using our feed?" style pop-up where professional users are restricted. In this case, the agent must ask the user whether Callistra has legal arrangements with the source to fetch documents from their system, and if yes, then the agent must proceed. This has happened in the case of ASX (Australian Securities Exchange), and in the case of LSEG RNS.

- Translation not available for the required language: The Agent must alert the user, and the user MUST resolve the translation issue FIRST, before proceeding with the production ingestion. While testing, translation is not required.

- Supplied company mapping CSVs do not contain enough coverage for the stock exchange: This is often expected, as coverage gaps/new listings can happen. Currently, the primary_symbol must be ingested correctly, regardless of it being present in the CSVs or not.

## OPENING A GPT CLASSIFICATION/TASK LOOP

The Agent can use the GPT-NANO (gpt-5-nano, cost: $0.05/Million Input tokens, $0.4/Million Output tokens) for various use cases, such as classification, document understand, etc. however, it is advised that these use cases should be offline, and not a runtime inference unless the USER says otherwise. To spawn the gpt-nano, use AZURE_OPENAI_API_KEY, with the appropriate AZURE endpoints/urls/ and deployment_name/model_name as "gpt-5-nano". 

The user would provide AZURE credentials in a .env file. In case AZURE credentials are missing/dont work/ mention this to the user, and they might provide an OPENAI credentials. 

The following code is how the gpt-nano is supposed to work:
<code>
import os
from dotenv import load_dotenv
load_dotenv(".env")

from openai import OpenAI

endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
api_version = os.environ["AZURE_OPENAI_API_VERSION"]
key = os.environ["AZURE_OPENAI_API_KEY"]

base_url = f"{endpoint}/openai/deployments/gpt-5-nano"
print("base_url host:", base_url.split("//")[1].split("/")[0])

client = OpenAI(
    api_key=key,
    base_url=base_url,
    default_query={"api-version": api_version},
)

resp = client.chat.completions.create(
    model="gpt-5-nano",
    messages=[{"role": "user", "content": "hi"}],
)
print(resp.choices[0].message.content)
</code>

Once you get a "hi" back from gpt-nano, you can proceed to use it. Mention the use case to the user first, and seek permissions. Also mention an estimated cost, which would usually be very small, but in case of large classification/ongoing tasks, a cost estimate is needed.

## NOTE:
#1: The new_ingestion_guidelines.md define the technical pipeline contract. 

#2: The new_stock_exchange_guidelines.md adds the stock-exchange specific policy.

#3: "OCR" means conversion of an initial, raw source into machine readable, embedding friendly data. "Who performs the OCR" depends on the "raw source", if the raw source itself is plain text/.xml/.html or any machine readable source, then the ingestion worker should have a small "OCR" module. And if the raw source is anything of the form of PDF, PPT, WORD, IMAGE, or etc., then the OCR RUNNER would perform the "OCR", and the ingestion worker would only ingest the document.

#### The End.


