# ADR-010 validation: Qwen vs the 30 hand-judged filings

Prompt v1. Truth: REAL/PARTIAL = true, BOILERPLATE = false. **Agreement: 27/30 = 90%** (ADR-010 gate: >= 80%). Average 10.6 s per call.

| | Qwen true | Qwen false |
|---|---|---|
| truth true | 16 | 2 |
| truth false | 1 | 11 |

| cik | name | year | truth | qwen sells_into | role | confidence | agree | evidence |
|---|---|---|---|---|---|---|---|---|
| 1356104 | Mellanox Technologies, Ltd. | 2015 | True | True | networking | high | yes | We are a fabless semiconductor company that designs, manufactures and sells high-performance interconnect products and solutions primarily based on the InfiniBand and Ethernet standards... focused on… |
| 746838 | UNISYS CORP | 2011 | True | True | compute | high | yes | we design and develop servers and related products to help clients reduce costs and improve the efficiency of their data center environments... Product offerings include enterprise-class servers, such |
| 2488 | ADVANCED MICRO DEVICES INC | 2021 | True | True | compute | high | yes | x86 microprocessors, as standalone devices or as incorporated into an accelerated processing unit (APU), chipsets, discrete and integrated graphics processing units (GPUs), data center and |
| 1288469 | MAXLINEAR, INC | 2020 | True | True | networking | high | yes | fiber-optic modules for data center, metro, and long-haul transport networks; high speed optical interconnect products provide the interconnect function between the servers to the top-of-rack switch |
| 1739936 | MOMENTIVE GLOBAL INC. | 2019 | False | False | none | high | yes | SurveyMonkey is a leading global provider of survey software products and purpose-built solutions that enable organizations to engage with their key constituents |
| 1107694 | RACKSPACE HOSTING, INC. | 2016 | True | True | cloud | high | yes | the Company is the world leader in the managed cloud segment of the business information technology ("IT") market... we offer a broad portfolio of integrated IT solutions... dedicated cloud and public |
| 50863 | INTEL CORP | 2015 | True | True | compute | high | yes | strive to ensure that the Company technology is the best choice across the compute continuum, including PCs, data centers, ultra-mobile devices, and the Internet of Things |
| 1650372 | Atlassian Corp | 2026 | False | False | none | high | yes | the Company's team collaboration software enables organizations to connect all teams through a system of work that unlocks productivity at scale |
| 1044435 | P10 Industries, Inc. | 2014 | True | True | power_energy | high | yes | the Company designs, manufactures, and sells flywheel-based uninterruptible power supply ("UPS") products and modular infrastructure solutions...designed to ensure continuity for data centers |
| 1493594 | MACOM Technology Solutions Holdings, Inc. | 2024 | True | False | none | medium | **no** | We have one reportable operating segment that designs, develops, manufactures and markets semiconductors and modules. |
| 1045810 | NVIDIA CORP | 2011 | True | True | compute | high | yes | the high performance computing market with our Tesla computing solutions products; These CPU cores will be integrated into future generation GPUs for PCs, servers, and supercomputers |
| 949158 | CRAY INC | 2011 | True | True | compute | medium | yes | We design, develop, manufacture, market and service high-performance computing, or HPC, systems, commonly known as supercomputers... We focus our sales and marketing activities on government agencies, |
| 1077866 | LOOKSMART LTD | 2014 | False | False | none | high | yes | the Company is a digital advertising solutions company that provides relevant solutions for search and display advertising customers |
| 1651235 | Acacia Communications, Inc. | 2019 | True | True | networking | high | yes | optical interconnect modules with transmission speeds ranging from 100 to 1,200 gigabits per second, or Gbps, for use in long-haul, metro and inter-data center markets |
| 1394056 | ONE STOP SYSTEMS, INC. | 2019 | True | True | compute | high | yes | providing custom servers, compute accelerators, solid-state storage arrays and system expansion systems... high-end GPU accelerators and high-performance flash arrays |
| 1851003 | Zeta Global Holdings Corp. | 2026 | False | False | none | high | yes | We depend on third-party data centers, systems and technologies to operate our business; Our business and the effectiveness of our platform depends on our ability to collect and use data online |
| 1297996 | DIGITAL REALTY TRUST, INC. | 2011 | True | True | datacenter_infra | high | yes | We provide flexible, customer oriented solutions designed to meet the needs of technology and corporate data center users, including Turn-Key Datacenter, Powered Base Building and build-to-suit |
| 1108524 | Salesforce, Inc. | 2018 | False | False | none | high | yes | Einstein AI is a set of advanced AI capabilities embedded across the entire the Company Platform that automatically discovers relevant insights, predicts future behavior |
| 1528396 | Guidewire Software, Inc. | 2024 | False | False | none | high | yes | Our core products are InsuranceSuite Cloud, InsuranceNow, and InsuranceSuite for self-managed installations. These products are transactional systems of record that support the entire insurance |
| 1101239 | EQUINIX INC | 2019 | True | True | datacenter_infra | high | yes | the Company connects more than 9,800 companies directly to their customers and partners across the world's most interconnected data center and interconnection platform |
| 1595974 | MAGNITE, INC. | 2025 | False | False | none | high | yes | provides technology solutions to automate the purchase and sale of digital advertising inventory |
| 1373715 | ServiceNow, Inc. | 2021 | False | False | none | high | yes | We market and sell our products and services to enterprises across industries, including government, financial services, healthcare, telecommunications, manufacturing, IT services, technology, oil and |
| 746838 | UNISYS CORP | 2009 | True | True | datacenter_infra | high | yes | In the Technology segment, we design and develop servers and related services and products that help clients modernize their data center environments to reduce costs and improve efficiency. |
| 1577966 | COPT DEFENSE PROPERTIES, L.P. | 2017 | True | True | datacenter_infra | high | yes | We own, manage, lease, develop and selectively acquire office and data center properties... 13 triple-net leased, single-tenant data center properties... a wholesale data center with a critical load |
| 6951 | APPLIED MATERIALS INC /DE | 2024 | False | True | semis_equipment | medium | **no** | Our customers' products are used in a wide variety of products such as personal computing devices, mobile phones, artificial intelligence (AI) and data center servers |
| 796343 | ADOBE INC. | 2023 | False | False | none | high | yes | uses insights driven by artificial intelligence ("AI") to enable the delivery of personalized digital experiences across various channels in milliseconds |
| 1078271 | EXTREME NETWORKS INC | 2016 | True | True | networking | high | yes | a complete line of switches to cover the campus, right up to and including the data center. As network switching leaders in enterprise, datacenter and cloud |
| 1410384 | Q2 Holdings, Inc. | 2019 | False | False | none | high | yes | Our solutions and our data center infrastructure and resources are designed to comply with the stringent security and technical regulations applicable to financial institutions |
| 1078271 | EXTREME NETWORKS INC | 2022 | True | False | none | medium | **no** | expand our portfolio with new cloud-managed SD-WAN and security offerings to support our enterprise customers |
| 1393052 | VEEVA SYSTEMS INC | 2014 | False | False | none | high | yes | Veeva is a leading global provider of industry-specific, cloud-based software solutions for the life sciences industry |
