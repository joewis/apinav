# API Ninjas plugin
# API Ninjas (https://api-ninjas.com/api) provides 140+ individual micro-APIs.
# This plugin exposes those micro-APIs as apinav catalog entries so the user can
# discover them through the meta-search engine. A valid API_NINJAS_API_KEY is
# required to *call* the endpoints, but listing/discovery itself does not use it.
import sys, os, re
sys.path.insert(0, os.path.dirname(__file__))


SOURCE = "api-ninjas"


# Catalog of API Ninjas micro-APIs. Each row is an API entry that apinav can
# index and search. Derived from https://api-ninjas.com/api (142 endpoints).
_ENDPOINTS = [
    ("Balance Sheet", "Get balance sheet data for any public company including assets, liabilities, equity, and debt."),
    ("Bank Holidays", "Get bank holiday dates for over 230 countries and territories around the world."),
    ("BIN", "BIN lookup for credit card brands, types, and more."),
    ("Bitcoin", "Live and historical Bitcoin prices, plus which companies and ETFs hold bitcoin according to their SEC filings."),
    ("BSB", "Look up Australian BSB numbers to find the bank, branch, address, and supported payment systems."),
    ("Canada Routing Number", "Look up Canadian bank information by routing number, transit number, or institution number."),
    ("Cash Flow", "Get cash flow statement data for any public company including operating, investing, and financing cash flows."),
    ("Commodity Price", "Real-time prices for dozens of commonly-traded commodities."),
    ("Convert Currency", "Convert between currencies using current exchange rates."),
    ("Crypto Price", "Current and historical prices for any cryptocurrency, plus the crypto holdings public companies report to the SEC."),
    ("Earnings", "Get earnings report data for any company, or for all companies by filing date."),
    ("Earnings Calendar", "Earnings result announcements for all major companies."),
    ("Earnings Call Transcript", "Transcript of earnings calls for all major companies."),
    ("ETF", "Get information on ETFs including holdings, price, and more."),
    ("Exchange Rate", "Get current and historical exchange rates for any currency pair."),
    ("Executive Compensation", "Named-executive-officer pay (salary, bonus, stock & option awards, total) for US public companies, straight from SEC proxy filings."),
    ("Gold Price", "Real-time price for gold futures."),
    ("IBAN", "Look up any International Bank Account Number (IBAN)."),
    ("Income Statement", "Get income statement data for any public company including revenue, gross profit, operating income, net income, and EPS."),
    ("Income Tax", "Get current and historical income tax rates."),
    ("Income Tax Calculator", "Detailed income tax calculator for the US and Canada, plus per-paycheck W-4 withholding."),
    ("Inflation", "Get current inflation data for the dozens of countries."),
    ("Insider Trading", "Get insider trading data for any company."),
    ("Institutional Holdings", "Institutional investment manager holdings from SEC Form 13F filings — portfolios by manager, holders by stock, and ownership analytics."),
    ("Interest Rate", "Get current interest rates from all central banks and benchmarks."),
    ("IPO", "Past, current, and upcoming IPOs for U.S. companies sourced directly from SEC EDGAR."),
    ("LIBOR", "Get historical LIBOR rates for all tenors (current rates discontinued)."),
    ("Market Cap", "Real-time market cap data for all companies in major exchanges."),
    ("Mortgage Calculator", "Simple-yet-powerful mortgage calculator for home financing."),
    ("Mortgage Rate", "Current and historical daily mortgage rate data."),
    ("Mutual Fund", "Lookup mutual fund information for any ticker."),
    ("NAICS Code", "Look up any NAICS industry code: official title, description, hierarchy and index terms, plus SBA size standards, rule-based classification, and industry risk flags."),
    ("Oil Price", "Real-time and historical prices for crude oil (WTI & Brent), natural gas, heating oil, and RBOB gasoline."),
    ("Property Tax", "Get property tax rate data for different cities, zip codes and counties."),
    ("Routing Number", "Lookup and search for routing numbers for over 28,000 US banks."),
    ("Sales Tax", "Get sales tax rates for any location in the USA, plus economic nexus thresholds, sales tax holidays and filing due dates for all 50 states and DC."),
    ("Sales Tax Calculator", "Detailed sales tax calculator for any region in the USA."),
    ("Sanctions Screening", "Screen a person, entity, vessel, or aircraft against major government sanctions lists with fuzzy name matching."),
    ("SEC", "Search millions of SEC filings from public companies."),
    ("Short Interest", "Bi-weekly US short interest for 22,000+ stocks and ETFs, with short percent of float, days to cover, daily short volume, and fails-to-deliver."),
    ("Sort Code", "Look up and validate any UK sort code. Free sort code checker and sort code lookup for every UK bank branch."),
    ("Stock Exchange", "Key information about every stock exchange around the world."),
    ("Stock News", "Latest stock-market news headlines from major financial publishers, optionally filtered by ticker."),
    ("Stock Price", "Real-time stock prices for all companies and exchanges."),
    ("Stock Split", "Historical and upcoming stock splits for companies listed on the world's largest stock exchanges."),
    ("SWIFT Code", "Search SWIFT Codes for hundreds of thousands of bank branches."),
    ("Ticker", "Lookup company information for given ticker symbols and search for ticker symbols by company name."),
    ("Treasury Yield", "Get the current and historical U.S. Treasury yield curve: par (CMT), real (TIPS), and T-bill rates, with 2s10s / 3m10s spreads."),
    ("Unemployment", "Get current and historical unemployment data for any country."),
    ("VAT Rates", "Get current and historical VAT tax rates for any country in the European Union."),
    ("VAT Validation", "Validate any EU VAT number — instant format and checksum checks, live EU registry status, company name and address, and audit-ready consultation numbers."),
    ("Barcode", "Generate barcode images for any text."),
    ("Disposable Email Checker", "Check whether an email address is from a disposable email provider."),
    ("DNS Lookup", "Look up DNS records for any domain."),
    ("Domain", "Check domain availability and retrieve basic registration information."),
    ("IP Lookup", "Lookup location information for any IP address."),
    ("MX Lookup", "Lookup MX records for any domain."),
    ("Password Generator", "Generate random passwords that are hard to guess."),
    ("Phone Lookup", "Look up a phone number's carrier, line type, VOIP status, and MCC/MNC — worldwide, from free public data."),
    ("QR Code", "Generate custom QR codes for any data."),
    ("URL Lookup", "Lookup location information for any URL domain."),
    ("User Agent", "Parse and generate user agent strings."),
    ("Validate Email", "Check whether an email address is valid and get its metadata."),
    ("Validate Phone", "Check whether a phone number is valid and get its metadata."),
    ("Web Scraper", "Web scraper API to retrieve HTML and plaintext data from any website URL."),
    ("Webpage", "Retrieve URL information and web page metadata from any website URL."),
    ("Whois", "Look up domain registry information using WHOIS protocol for any domain."),
    ("Embeddings", "Encode any text to vectors using state-of-the-art machine learning models."),
    ("Face Detect", "Detect faces from any given image."),
    ("Image to Text", "State-of-the-art text detection and extraction from images."),
    ("Object Detection", "Fast and accurate image object recognition using the latest machine learning algorithms."),
    ("Sentiment", "Text sentiment analysis using state-of-the-art algorithms."),
    ("Text Similarity", "Compute text similarity score using the latest NLP machine learning models."),
    ("Animals", "Detailed and interesting facts for thousands of animal species."),
    ("Cats", "Complete list of information for every cat breed."),
    ("Dogs", "All you need to know about every breed of man's best friend."),
    ("Advice", "Get a random piece of life advice."),
    ("Bucket List", "Inspirational bucket list ideas for every type of person."),
    ("Celebrity", "Juicy information on famous celebrities including entertainers, athletes, and politicians."),
    ("Chuck Norris", "Thousands of funny Chuck Norris jokes for your entertainment apps."),
    ("Dad Jokes", "Thousands of hilarious dad jokes at your disposal."),
    ("Day in History", "Get historical events that happened on a specific date."),
    ("Emoji", "Get image and metadata information for every Unicode emoji."),
    ("Facts", "Access our database of over 500,000 interesting facts."),
    ("Hobbies", "Thousands of hobby ideas for people of all ages."),
    ("Horoscope", "Get daily horoscopes for all zodiac signs."),
    ("Jokes", "Funny jokes perfect for a comedy app or to entertain your friends."),
    ("Quotes", "Access over 50,000 quotes from famous people throughout history."),
    ("Riddles", "Quality riddles perfect for entertainment apps."),
    ("Sudoku", "Generate and solve Sudoku puzzles."),
    ("Trivia", "Endless knowledge from our database of over 100,000 trivia questions and answers."),
    ("Calories Burned", "Calories burned calculator for hundreds of different sports/activities."),
    ("Cocktail", "Search thousands of cocktail recipes."),
    ("Covid-19", "Covid-19 case count and death data for every country in the world."),
    ("Exercises", "Get workout exercises for every muscle group."),
    ("Hospitals", "Get detailed information about hospitals in the United States."),
    ("Nutrition", "Extract nutrition data from text with our natural language processing algorithms."),
    ("Recipe", "Look up over 200,000 recipes from cuisines around the world."),
    ("Baby Names", "Popular and unique baby name name generator for boys, girls, and gender-neutral names."),
    ("Counter", "Custom numerical counters to keep track of anything."),
    ("Historical Events", "Search through the most famous events in history."),
    ("Historical Figures", "Get vital information on the most famous people in history."),
    ("Holidays", "Get holiday dates for every country until 2030."),
    ("Logo", "Logo images for tens of thousands of different companies."),
    ("Planets", "Astronomy info on every planet discovered in our universe."),
    ("Public Holidays", "Get public holidays for any country and year for over 100 different countries."),
    ("Random Image", "Random high-quality images for your design needs."),
    ("Random User", "Random user data generator for placeholders and testing."),
    ("Stars", "Astronomy info on every star discovered in our universe."),
    ("Unit Conversion", "Convert units of measurement between different systems."),
    ("University", "Get information about universities in the United States and Canada."),
    ("Air Quality", "Get current air quality information including AQI and major pollutants."),
    ("City", "Useful statistics for tens of thousands of cities around the world."),
    ("Country", "Key statistics for over 200 countries and regions around the world."),
    ("Country Flag", "Get flag images for any country."),
    ("County", "Search detailed information for every county in the United States."),
    ("GDP", "Get current and historical GDP data for any country."),
    ("Geocoding", "Convert cities and US ZIP codes to coordinates and back, enriched with timezone, administrative regions, country data, elevation and more."),
    ("Population", "Historical, current, and projected population data for over 200 countries and regions around the world."),
    ("Postal Code", "Look up postal codes in over 100 countries worldwide, returning city, region, and coordinates."),
    ("Timezone", "Timezone data for any location on the planet."),
    ("Weather", "Access current weather data for hundreds of thousands of regions in the world."),
    ("Working Days", "Lookup the number of working days in a given month or year for any country."),
    ("World Time", "Get the current time for any location in the world."),
    ("Zip Code", "Search and get detailed information for every ZIP code in the United States."),
    ("Dictionary", "Look up any word in the English dictionary."),
    ("Lorem Ipsum", "Generate lorem ipsum placeholder text for your application."),
    ("Profanity Filter", "Detect and censor profanity in text."),
    ("Random Word", "Random word generator full of unique, interesting words."),
    ("Rhyme", "Look up rhyming words for any English word."),
    ("Spell Check", "Check spelling and get corrections for any text."),
    ("Text Language", "Detects the language of any input text."),
    ("Thesaurus", "Get synonym and antonyms for any word."),
    ("Aircraft", "Detailed technical specs for over 1,000 airplane models."),
    ("Airports", "Access vital data for over 85,000 airports, heliports, and airfields worldwide."),
    ("Cars", "Detailed data on tens of thousands of vehicles from over four hundred automakers, as a make/model/generation/body/trim drilldown."),
    ("Electric Vehicle", "Get information on electric vehicles including range, battery capacity, charging time and more."),
    ("EV Charger", "Search for electric vehicle chargers by location in countries around the world."),
    ("Helicopter", "Detailed technical specs for hundreds of helicopter models."),
    ("Motor Carrier", "Look up any US trucking company by USDOT or MC number: authority, insurance, safety rating, and inspection history."),
    ("Motorcycles", "Detailed technical specifications on tens of thousands of motorcycle models."),
    ("VIN Lookup", "Find vehicle information from Vehicle Identification Numbers."),
]


def _slug(name: str) -> str:
    """Convert display name to the URL path used by api-ninjas.com docs."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def search(query, limit=5):
    """Return matching API Ninjas micro-APIs as catalog entries.

    API Ninjas does not expose a public \"search across all APIs\" endpoint, so
    this plugin maintains a static catalog of its 140+ micro-APIs. The query
    is matched against name and description (case-insensitive). A valid
    API_NINJAS_API_KEY is required to call the endpoints, but discovery is free.
    """
    q = query.lower()
    results = []
    for name, description in _ENDPOINTS:
        text = f"{name} {description}".lower()
        if q in text:
            slug = _slug(name)
            results.append(
                {
                    "name": name,
                    "description": description,
                    "source": SOURCE,
                    "category": "api-ninjas",
                    "auth": "apiKey",
                    "url": f"https://api-ninjas.com/api/{slug}",
                }
            )
            if len(results) >= limit:
                break
    return results