# Spot Check Evaluation: 10-K Keyword Exposure Verification

This report provides a line-by-line spot-check audit of 30 randomly sampled 10-K filings identified as "AI/datacenter-exposed" by keyword density under ADR-007 (`research/pit_universe/dictionary.json`). Each filing was evaluated by inspecting Item 1 (Business), identifying actual products and services sold, and examining dictionary keyword contexts across Item 1, Item 1A (Risk Factors), and MD&A.

Filings are judged into three categories:
1. **REAL**: The company sells products/services into AI, datacenters, GPUs/accelerators, networking for datacenters, datacenter power/cooling.
2. **PARTIAL**: Genuine commercial business in the target domain, but represents a secondary or minor part of the overall business.
3. **BOILERPLATE**: Terms appear only in risk factors, generic IT mentions ("our data center", "we use machine learning internally"), brand name collisions, or vocabulary-era risk disclosures.

---

## Evaluation Table

| CIK | Name | Year | Verdict | One-Line Evidence |
| :--- | :--- | :---: | :---: | :--- |
| 1356104 | Mellanox Technologies, Ltd. | 2015 | **REAL** | Sells InfiniBand and Ethernet interconnect adapters, switches, and silicon photonics for enterprise data centers, cloud, and HPC. |
| 746838 | UNISYS CORP | 2011 | **PARTIAL** | Sells ClearPath enterprise servers and data center transformation/management services, but as a secondary pillar within a broad legacy IT consulting and BPO business. |
| 2488 | ADVANCED MICRO DEVICES INC | 2021 | **REAL** | Sells EPYC server CPUs, Radeon Instinct GPUs, and MI100 accelerators for cloud data centers, deep learning, and HPC workloads. |
| 1288469 | MAXLINEAR, INC | 2020 | **REAL** | Sells high-speed optical interconnect PAM4 DSPs, PHYs, and RF chips for data center and hyperscale networking infrastructure. |
| 1739936 | MOMENTIVE GLOBAL INC. | 2019 | **BOILERPLATE** | Online survey SaaS describing its primary data center in Las Vegas and using ML internally for survey design templates. |
| 1107694 | RACKSPACE HOSTING, INC. | 2016 | **REAL** | Sells managed cloud, dedicated server hosting, and colocation infrastructure across 11 company-operated data centers. |
| 50863 | INTEL CORP | 2015 | **REAL** | Operates the Data Center Group (DCG), selling Xeon processors, server chips, and storage solutions for enterprise/cloud data centers and HPC. |
| 1650372 | Atlassian Corp | 2026 | **BOILERPLATE** | Enterprise team collaboration software vendor (Jira/Confluence) with on-prem edition branded "Data Center" and embedded internal AI features. |
| 1044435 | P10 Industries, Inc. | 2014 | **REAL** | Operating as Active Power, sells flywheel-based UPS systems and modular infrastructure solutions directly into data centers and hyperscalers. |
| 1493594 | MACOM Technology Solutions Holdings, Inc. | 2024 | **REAL** | Sells analog ICs, lasers, photodetectors, and optical components for 400G/800G/1.6T data center optical interconnects. |
| 1045810 | NVIDIA CORP | 2011 | **REAL** | Designs and sells GPUs, Tesla GPU computing accelerators, and CUDA parallel architecture for HPC supercomputing. |
| 949158 | CRAY INC | 2011 | **REAL** | Designs, builds, and services HPC supercomputers integrating custom interconnects, GPU accelerators, and liquid cooling. |
| 1077866 | LOOKSMART LTD | 2014 | **BOILERPLATE** | Search advertising network describing an acquired 10,000 sq ft facility in Phoenix used to house its own internal ad servers. |
| 1651235 | Acacia Communications, Inc. | 2019 | **REAL** | Develops and sells coherent optical interconnect modules, DSP ASICs, and silicon PICs for 400G data center interconnects (DCI). |
| 1394056 | ONE STOP SYSTEMS, INC. | 2019 | **REAL** | Designs and manufactures high-performance GPU compute accelerators, PCIe expansion systems, and servers for AI/ML and HPC. |
| 1851003 | Zeta Global Holdings Corp. | 2026 | **BOILERPLATE** | Marketing automation cloud software using GenAI internally for ad targeting, with a false positive match on "sales accelerator". |
| 1297996 | DIGITAL REALTY TRUST, INC. | 2011 | **REAL** | Data center REIT owning, developing, and leasing Turn-Key Datacenters, powered base buildings, and colocation facilities. |
| 1108524 | Salesforce, Inc. | 2018 | **BOILERPLATE** | Enterprise CRM cloud software vendor using third-party data centers for hosting and embedding AI capabilities internally into its software suite. |
| 1528396 | Guidewire Software, Inc. | 2024 | **BOILERPLATE** | Vertical SaaS for P&C insurance carriers with ML/GenAI mentions primarily confined to 2024-era risk factor disclosures. |
| 1101239 | EQUINIX INC | 2019 | **REAL** | Global datacenter REIT operating 200+ International Business Exchange (IBX) colocation and interconnection data centers. |
| 1595974 | MAGNITE, INC. | 2025 | **BOILERPLATE** | Digital ad exchange (SSP) operating on-premise servers in colocation data centers and using ML internally for auction bid filtering. |
| 1373715 | ServiceNow, Inc. | 2021 | **BOILERPLATE** | Enterprise workflow software vendor hosting its SaaS in global colocation data centers and incorporating AI/ML workflow automation tools. |
| 746838 | UNISYS CORP | 2009 | **PARTIAL** | Sells ClearPath enterprise servers and data center outsourcing/transformation services, but as a secondary pillar within a broad legacy IT services and BPO business. |
| 1577966 | COPT DEFENSE PROPERTIES, L.P. | 2017 | **PARTIAL** | Government/defense office REIT owning a dedicated 14-property data center shell and 19.25 MW wholesale data center portfolio alongside core office assets. |
| 6951 | APPLIED MATERIALS INC /DE | 2024 | **BOILERPLATE** | Semiconductor wafer fab equipment manufacturer whose keyword density is driven by 2024-era internal AI risk factors and commentary on AI driving chip demand. |
| 796343 | ADOBE INC. | 2023 | **BOILERPLATE** | Creative and document SaaS provider integrating AI/ML features (Adobe Sensei) internally into its software suite, with generic data center risk factors. |
| 1078271 | EXTREME NETWORKS INC | 2016 | **REAL** | Designs and sells wired enterprise network infrastructure equipment, including Ethernet switches and management software for data centers. |
| 1410384 | Q2 Holdings, Inc. | 2019 | **BOILERPLATE** | Digital banking software-as-a-service vendor describing its leased Texas data centers where it hosts banking applications. |
| 1078271 | EXTREME NETWORKS INC | 2022 | **REAL** | Sells enterprise and cloud networking equipment including ExtremeSwitching Ethernet switches for data center environments. |
| 1393052 | VEEVA SYSTEMS INC | 2014 | **BOILERPLATE** | Life sciences cloud software provider using third-party data centers (primarily Salesforce infrastructure) to host its SaaS applications. |

---

## Summary & Findings

### Verdict Counts

- **REAL**: 15 / 30 (50.0%)
- **PARTIAL**: 3 / 30 (10.0%)
- **BOILERPLATE**: 12 / 30 (40.0%)

*(Note: If Applied Materials is classified as PARTIAL due to wafer fab equipment producing AI/datacenter chips, counts become REAL: 15 (50%), PARTIAL: 4 (13.3%), BOILERPLATE: 11 (36.7%).)*

---

### Systematic False Positives & Scanner Failure Modes

Analysis of the 12 BOILERPLATE and borderline filings reveals six recurring, systematic mechanisms that artificially inflate keyword density:

1. **SaaS Hosting & Infrastructure Consumption ("Our Data Center" / "Colocation")**:
   - **Mechanism**: Cloud and enterprise software providers (Salesforce, ServiceNow, Veeva Systems, Q2 Holdings, Momentive/SurveyMonkey, Magnite) describe where their software runs. They describe leased colocation facilities ("our data center in Las Vegas", "two Tier 4 data centers in Texas") and include extensive risk disclosures regarding power interruptions, connectivity failures, and SLAs at third-party data center providers.
   - **Impact**: Generates 20–100+ data center keyword hits per filing for companies that sell application-layer software, not data center infrastructure.

2. **Product Tier / Brand Name Collisions**:
   - **Mechanism**: Atlassian Corp (CIK 1650372) uses the term "Data Center" repeatedly (32 hits) as a product brand name for its self-managed on-premises enterprise software editions ("Jira Data Center", "Confluence Data Center").
   - **Impact**: Pure brand-name false positive; the company sells team collaboration tools.

3. **Internal AI/ML Adoption vs. Commercial AI Selling ("We Use Machine Learning Internally")**:
   - **Mechanism**: Application software vendors routinely advertise that their internal product features are powered by ML or AI algorithms (e.g., Adobe's "Adobe Sensei" for photo editing, Zeta Global's "ZMP" for ad targeting, Magnite's bid filtering, Momentive's survey question recommendations).
   - **Impact**: The companies are AI *consumers* embedding basic statistical models into SaaS, not AI infrastructure or AI foundation model providers.

4. **Vocabulary-Era Risk Factor Inflation (2024–2026 Filings)**:
   - **Mechanism**: Following SEC guidance and the rise of generative AI, recent filings (2024–2026) show dense clusters of terms like `artificial intelligence`, `generative AI`, and `large language models` inside Item 1A (Risk Factors).
   - **Examples**:
     - *Guidewire Software (2024)*: 33 out of 36 hits occur in risk factors outlining liabilities from AI hallucinations, copyright, and third-party LLMs.
     - *Applied Materials (2024)*: 17 out of 23 AI hits occur in a single boilerplate risk factor discussing risks of internal employee and competitor AI adoption.
     - *Zeta Global (2026)*: Contains repeated risk disclosures regarding GenAI implementation risks.

5. **Polysemy and Idiomatic Collocations**:
   - **Mechanism**: Words like "accelerator" appearing in non-hardware contexts.
   - **Example**: In Zeta Global, "accelerator" matched on *"our One Zeta model also serves as a sales accelerator to help acquire and grow new customers"*.

6. **Self-Hosting Corporate Facilities**:
   - **Mechanism**: LookSmart (CIK 1077866) purchased a 10,000 sq ft commercial facility in Phoenix to migrate its search advertising servers off third-party colocation. The transaction was described across Item 1, MD&A, and property notes, creating 26 hits without selling any data center services.
