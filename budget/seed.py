"""Built-in categories, merchant dictionary, and merchant-category-code (MCC) fallbacks.

Only general, nationally known merchants live here. Household-specific entries (local
businesses, extra categories, keywords) go in data/personal.toml; see personal.example.toml.

The dictionary is US-centric: national US chains and services, plus some regional chains. Elsewhere,
most names won't match until you add your own merchants (in the app or in personal.toml).

Everything is loaded with INSERT OR IGNORE, so edits made in the app are never overwritten,
and new built-in entries added here show up on the next start.
"""
import sys

CATEGORIES = [
    # Income
    ("Paycheck", "income"),
    ("Rental Income", "income"),
    ("Side Income", "income"),
    ("Other Income", "income"),  # refunds, reimbursements, interest, gifts received
    # Spending
    ("Mortgage & HOA", "expense"),
    ("Bills & Utilities", "expense"),  # utilities, internet, phone, subscriptions, insurance
    ("Home & Garden", "expense"),
    ("Groceries", "expense"),
    ("Dining & Drinks", "expense"),
    ("Auto & Gas", "expense"),
    ("Health & Fitness", "expense"),
    ("Kids & Education", "expense"),
    ("Pets", "expense"),
    ("Shopping & Personal", "expense"),  # shopping, clothes, hygiene
    ("Hobbies & Fun", "expense"),  # outdoors, entertainment
    ("Travel", "expense"),
    ("Giving", "expense"),  # donations and gifts
    ("Taxes & Fees", "expense"),
    ("Other", "expense"),
    # Money moving between your own accounts (left out of spending/income totals)
    ("Transfer", "transfer"),
    ("Savings & Investments", "transfer"),
]

# How spending counts toward a month (see budgeting.py). Expense categories not listed are flexible.
SPENDING_GROUPS = [
    ("fixed", "Fixed", "bills that barely change: mortgage, utilities, insurance"),
    ("flexible", "Flexible", "day-to-day spending you steer: groceries, dining, shopping, hobbies"),
    ("nonmonthly", "Non-monthly", "lumpy costs: projects, travel, taxes"),
]
CATEGORY_GROUPS = {
    "Mortgage & HOA": "fixed",
    "Bills & Utilities": "fixed",
    "Home & Garden": "nonmonthly",
    "Travel": "nonmonthly",
    "Taxes & Fees": "nonmonthly",
    "Savings & Investments": "saving",  # a transfer that counts as money invested
}


def category_group(name, kind):
    if kind == "expense":
        return CATEGORY_GROUPS.get(name, "flexible")
    if kind == "transfer":
        return CATEGORY_GROUPS.get(name)
    return None


# Categories from earlier versions, folded into the broader ones above. Databases are migrated once
# (db.migrate), and personal.toml files that still use an old name keep working.
MERGED = {
    "Refunds & Reimbursements": "Other Income",
    "Interest & Dividends": "Other Income",
    "Gifts Received": "Other Income",
    "Utilities & Internet": "Bills & Utilities",
    "Subscriptions": "Bills & Utilities",
    "Insurance": "Bills & Utilities",
    "Eating Out": "Dining & Drinks",
    "Drinks & Bars": "Dining & Drinks",
    "Health & Medical": "Health & Fitness",
    "Fitness": "Health & Fitness",
    "Kids & School": "Kids & Education",
    "Education & Tuition": "Kids & Education",
    "Student Loans": "Kids & Education",
    "Shopping": "Shopping & Personal",
    "Clothes & Hygiene": "Shopping & Personal",
    "Outdoor & Hunting": "Hobbies & Fun",
    "Entertainment": "Hobbies & Fun",
    "Donations": "Giving",
    "Gifts Given": "Giving",
    "Cash & ATM": "Other",
    "Wedding": "Other",
    "Credit Card Payment": "Transfer",
}


def category_name(name):
    """The current name for a category, following MERGED."""
    return MERGED.get(name, name)

# Categories counted on the nearest 1st of the month, so rent paid on the 30th lands in the month it's for.
SNAP_TO_MONTH = {"Rental Income", "Mortgage & HOA"}

# (pattern found in the bank's raw description, clean name, category)
BUILTIN_RULES = [
    # Card payments, transfers, payroll
    ("PAYMENT THANK YOU", "Credit Card Payment", "Transfer"),
    ("INTERNET PAYMENT", "Credit Card Payment", "Transfer"),
    ("CARDMEMBER SERV", "US Bank Card Payment", "Transfer"),
    ("CHASE CREDIT CRD", "Chase Card Payment", "Transfer"),
    ("AUTOPAY PAYMENT", "Credit Card Payment", "Transfer"),
    ("PAYMENT TO CREDIT CARD", "Credit Card Payment", "Transfer"),
    ("MOBILE BANKING PAYMENT TO CREDIT CARD", "Credit Card Payment", "Transfer"),
    ("CUSTOMER WITHDRAWAL", "Cash Withdrawal", "Other"),
    ("MONTHLY MAINTENANCE FEE", "Monthly Maintenance Fee", "Taxes & Fees"),
    ("APPLE CASH", "Apple Cash", None),
    ("AMAZON DIGIT", "Amazon Digital", "Bills & Utilities"),
    ("AMZNFREETIME", "Amazon Kids+", "Bills & Utilities"),
    ("AMAZON MKTPLACE", "Amazon", "Shopping & Personal"),
    ("CLOUDFLARE", "Cloudflare", "Bills & Utilities"),
    ("APPLE.COM/US", "Apple", "Shopping & Personal"),
    ("THE UPS STORE", "The UPS Store", "Shopping & Personal"),
    ("MOBILE BANKING TRANSFER", "Transfer", "Transfer"),
    ("ONLINE TRANSFER", "Transfer", "Transfer"),
    ("INTERNET BANKING TRANSFER", "Transfer", "Transfer"),
    ("PAYROLL", "Paycheck", "Paycheck"),
    ("DIRECT DEP", "Paycheck", "Paycheck"),
    ("INTEREST PAID", "Interest", "Other Income"),
    ("ANNUAL MEMBERSHIP FEE", "Annual Card Fee", "Taxes & Fees"),
    ("ATM WITHDRAWAL", "ATM Withdrawal", "Other"),
    ("ZELLE", "Zelle", None),
    ("VENMO", "Venmo", None),
    ("MERRILL", "Merrill", "Savings & Investments"),
    ("ML ", "Merrill", "Savings & Investments"),
    ("SCHWAB", "Charles Schwab", "Savings & Investments"),
    ("VOYA", "Voya", "Savings & Investments"),
    ("FIDELITY", "Fidelity", "Savings & Investments"),
    ("FID BKG SVC", "Fidelity", "Savings & Investments"),
    ("VANGUARD", "Vanguard", "Savings & Investments"),
    ("COINBASE", "Coinbase", "Savings & Investments"),
    ("MOHELA", "Mohela Student Loans", "Kids & Education"),
    ("NAVIENT", "Navient Student Loans", "Kids & Education"),
    ("IRS USATAXPYMT", "IRS", "Taxes & Fees"),
    # Groceries & warehouse
    ("SAFEWAY", "Safeway", "Groceries"),
    ("WHOLEFDS", "Whole Foods", "Groceries"),
    ("WHOLE FOODS", "Whole Foods", "Groceries"),
    ("TRADER JOE", "Trader Joe's", "Groceries"),
    ("SPROUTS", "Sprouts", "Groceries"),
    ("NATURAL GROCERS", "Natural Grocers", "Groceries"),
    ("ALDI", "Aldi", "Groceries"),
    ("PUBLIX", "Publix", "Groceries"),
    ("COSTCO WHSE", "Costco", "Groceries"),
    ("COSTCO GAS", "Costco Gas", "Auto & Gas"),
    ("COSTCO TRAVEL", "Costco Travel", "Travel"),
    ("SAMS CLUB", "Sam's Club", "Groceries"),
    ("WALMART", "Walmart", "Shopping & Personal"),
    ("WAL-MART", "Walmart", "Shopping & Personal"),
    ("WM SUPERCENTER", "Walmart", "Shopping & Personal"),
    ("TARGET", "Target", "Shopping & Personal"),
    # Gas, auto, tolls
    ("SHELL OIL", "Shell", "Auto & Gas"),
    ("CONOCO", "Conoco", "Auto & Gas"),
    ("PHILLIPS 66", "Phillips 66", "Auto & Gas"),
    ("7-ELEVEN", "7-Eleven", "Auto & Gas"),
    ("CIRCLE K", "Circle K", "Auto & Gas"),
    ("SINCLAIR", "Sinclair", "Auto & Gas"),
    ("MAVERIK", "Maverik", "Auto & Gas"),
    ("CHEVRON", "Chevron", "Auto & Gas"),
    ("EXXON", "Exxon", "Auto & Gas"),
    ("VALERO", "Valero", "Auto & Gas"),
    ("SPEEDWAY", "Speedway", "Auto & Gas"),
    ("KUM & GO", "Kum & Go", "Auto & Gas"),
    ("QUIKTRIP", "QuikTrip", "Auto & Gas"),
    ("CASEYS", "Casey's", "Auto & Gas"),
    ("HOLIDAY STATIONS", "Holiday Gas", "Auto & Gas"),
    ("TESLA SUPERCHARGER", "Tesla Supercharger", "Auto & Gas"),
    ("DISCOUNT-TIRE", "Discount Tire", "Auto & Gas"),
    ("DISCOUNT TIRE", "Discount Tire", "Auto & Gas"),
    ("JIFFY LUBE", "Jiffy Lube", "Auto & Gas"),
    ("VALVOLINE", "Valvoline", "Auto & Gas"),
    ("O'REILLY", "O'Reilly Auto Parts", "Auto & Gas"),
    ("OREILLY", "O'Reilly Auto Parts", "Auto & Gas"),
    ("AUTOZONE", "AutoZone", "Auto & Gas"),
    # Eating out
    ("BUFFALO WILD", "Buffalo Wild Wings", "Dining & Drinks"),
    ("CHIPOTLE", "Chipotle", "Dining & Drinks"),
    ("MCDONALD'S", "McDonald's", "Dining & Drinks"),
    ("TACO BELL", "Taco Bell", "Dining & Drinks"),
    ("WENDYS", "Wendy's", "Dining & Drinks"),
    ("CHICK-FIL-A", "Chick-fil-A", "Dining & Drinks"),
    ("JIMMY JOHNS", "Jimmy John's", "Dining & Drinks"),
    ("SUBWAY", "Subway", "Dining & Drinks"),
    ("STARBUCKS", "Starbucks", "Dining & Drinks"),
    ("DUTCH BROS", "Dutch Bros", "Dining & Drinks"),
    ("DOMINOS", "Domino's", "Dining & Drinks"),
    ("PIZZA HUT", "Pizza Hut", "Dining & Drinks"),
    ("PAPA JOHN", "Papa John's", "Dining & Drinks"),
    ("MARCOS PIZZA", "Marco's Pizza", "Dining & Drinks"),
    ("PANERA", "Panera", "Dining & Drinks"),
    ("QDOBA", "Qdoba", "Dining & Drinks"),
    ("POTBELLY", "Potbelly", "Dining & Drinks"),
    ("NOODLES", "Noodles & Co", "Dining & Drinks"),
    ("SONIC DRIVE", "Sonic", "Dining & Drinks"),
    ("BURGER KING", "Burger King", "Dining & Drinks"),
    ("ARBYS", "Arby's", "Dining & Drinks"),
    ("DAIRY QUEEN", "Dairy Queen", "Dining & Drinks"),
    ("DENNY'S", "Denny's", "Dining & Drinks"),
    ("IHOP", "IHOP", "Dining & Drinks"),
    ("CULVERS", "Culver's", "Dining & Drinks"),
    ("IN-N-OUT", "In-N-Out", "Dining & Drinks"),
    ("FIVE GUYS", "Five Guys", "Dining & Drinks"),
    ("RAISING CANES", "Raising Cane's", "Dining & Drinks"),
    ("PANDA EXPRESS", "Panda Express", "Dining & Drinks"),
    ("RED ROBIN", "Red Robin", "Dining & Drinks"),
    ("MOD PIZZA", "MOD Pizza", "Dining & Drinks"),
    ("FIREHOUSE SUBS", "Firehouse Subs", "Dining & Drinks"),
    ("JERSEY MIKES", "Jersey Mike's", "Dining & Drinks"),
    ("KRISPY KREME", "Krispy Kreme", "Dining & Drinks"),
    ("DOORDASH", "DoorDash", "Dining & Drinks"),
    ("UBER EATS", "Uber Eats", "Dining & Drinks"),
    ("GRUBHUB", "Grubhub", "Dining & Drinks"),
    # Drinks
    ("LIQUOR", None, "Dining & Drinks"),
    # Shopping
    ("AMAZON MKTPL", "Amazon", "Shopping & Personal"),
    ("AMAZON MKTPLACE PMTS", "Amazon Refund", "Other Income"),
    ("AMZN MKTP", "Amazon", "Shopping & Personal"),
    ("AMAZON.COM", "Amazon", "Shopping & Personal"),
    ("AMAZON RETA", "Amazon", "Shopping & Personal"),
    ("AMAZON RET", "Amazon", "Shopping & Personal"),
    ("AMAZON PRIME", "Amazon Prime", "Bills & Utilities"),
    ("PRIME VIDEO", "Prime Video", "Bills & Utilities"),
    ("AMAZON KIDS", "Amazon Kids+", "Bills & Utilities"),
    ("BEST BUY", "Best Buy", "Shopping & Personal"),
    ("HOBBY-LOBBY", "Hobby Lobby", "Shopping & Personal"),
    ("GOODWILL", "Goodwill", "Shopping & Personal"),
    ("ETSY", "Etsy", "Shopping & Personal"),
    ("EBAY", "eBay", "Shopping & Personal"),
    ("KOHLS", "Kohl's", "Shopping & Personal"),
    ("TJ MAXX", "TJ Maxx", "Shopping & Personal"),
    ("ROSS STORES", "Ross", "Shopping & Personal"),
    ("OLD NAVY", "Old Navy", "Shopping & Personal"),
    ("BUCKLE", "Buckle", "Shopping & Personal"),
    ("NORDSTROM", "Nordstrom", "Shopping & Personal"),
    ("EUROPEAN WAX", "European Wax Center", "Shopping & Personal"),
    ("HOME DEPOT", "Home Depot", "Home & Garden"),
    ("LOWES", "Lowe's", "Home & Garden"),
    ("ACE HARDWA", "Ace Hardware", "Home & Garden"),
    ("IKEA", "IKEA", "Home & Garden"),
    ("ASHLEYFURNITURE", "Ashley Furniture", "Home & Garden"),
    ("TRUGREEN", "TruGreen", "Home & Garden"),
    ("MOLLY MAID", "Molly Maid", "Home & Garden"),
    # Outdoor & fitness
    ("CABELAS", "Cabela's", "Hobbies & Fun"),
    ("CAB STORE", "Cabela's", "Hobbies & Fun"),
    ("SCHEELS", "Scheels", "Hobbies & Fun"),
    ("SPORTSMANS WAREHOUSE", "Sportsman's Warehouse", "Hobbies & Fun"),
    ("BASS PRO", "Bass Pro Shops", "Hobbies & Fun"),
    ("REI", "REI", "Hobbies & Fun"),
    ("PLANET FITNESS", "Planet Fitness", "Health & Fitness"),
    ("LIFE TIME", "Life Time", "Health & Fitness"),
    ("ORANGETHEORY", "Orangetheory", "Health & Fitness"),
    ("GNC", "GNC", "Health & Fitness"),
    # Entertainment & subscriptions
    ("BOWLERO", "Bowlero", "Hobbies & Fun"),
    ("AMC", "AMC Theatres", "Hobbies & Fun"),
    ("REGAL", "Regal Cinemas", "Hobbies & Fun"),
    ("ALAMO DRAFTHOUSE", "Alamo Drafthouse", "Hobbies & Fun"),
    ("TICKETMASTER", "Ticketmaster", "Hobbies & Fun"),
    ("AXS.COM", "AXS Tickets", "Hobbies & Fun"),
    ("STUBHUB", "StubHub", "Hobbies & Fun"),
    ("SEATGEEK", "SeatGeek", "Hobbies & Fun"),
    ("TOPGOLF", "Topgolf", "Hobbies & Fun"),
    ("SPOTIFY", "Spotify", "Bills & Utilities"),
    ("NETFLIX", "Netflix", "Bills & Utilities"),
    ("HULU", "Hulu", "Bills & Utilities"),
    ("DISNEYPLUS", "Disney+", "Bills & Utilities"),
    ("DISNEY PLUS", "Disney+", "Bills & Utilities"),
    ("GOOGLE STORAGE", "Google One", "Bills & Utilities"),
    ("GOOGLE ONE", "Google One", "Bills & Utilities"),
    ("YOUTUBE", "YouTube", "Bills & Utilities"),
    ("APPLE.COM/BILL", "Apple", "Bills & Utilities"),
    ("HBO MAX", "Max", "Bills & Utilities"),
    ("PARAMOUNT", "Paramount+", "Bills & Utilities"),
    ("PEACOCK", "Peacock", "Bills & Utilities"),
    ("AUDIBLE", "Audible", "Bills & Utilities"),
    ("GARMIN", "Garmin", "Bills & Utilities"),
    # House, utilities, insurance
    ("CENTURYLINK", "CenturyLink", "Bills & Utilities"),
    ("QUANTUM FIBER", "Quantum Fiber", "Bills & Utilities"),
    ("XCEL ENERGY", "Xcel Energy", "Bills & Utilities"),
    ("COMCAST", "Xfinity", "Bills & Utilities"),
    ("XFINITY", "Xfinity", "Bills & Utilities"),
    ("VERIZON", "Verizon", "Bills & Utilities"),
    ("T-MOBILE", "T-Mobile", "Bills & Utilities"),
    ("AT&T", "AT&T", "Bills & Utilities"),
    ("ADT SECURITY", "ADT", "Bills & Utilities"),
    ("STATE FARM", "State Farm", "Bills & Utilities"),
    ("GEICO", "GEICO", "Bills & Utilities"),
    ("PROGRESSIVE", "Progressive", "Bills & Utilities"),
    ("ALLSTATE", "Allstate", "Bills & Utilities"),
    # Health, pets, kids
    ("CVS/PHARMACY", "CVS", "Health & Fitness"),
    ("WALGREENS", "Walgreens", "Health & Fitness"),
    ("HAND AND STONE", "Hand & Stone Massage", "Health & Fitness"),
    ("THE JOINT CHIROPRACTIC", "The Joint Chiropractic", "Health & Fitness"),
    ("CHEWY", "Chewy", "Pets"),
    ("PETSMART", "PetSmart", "Pets"),
    ("PETCO", "Petco", "Pets"),
    ("SCHOLASTIC", "Scholastic", "Kids & Education"),
    # Travel
    ("UNITED.COM", "United Airlines", "Travel"),
    ("SOUTHWES", "Southwest Airlines", "Travel"),
    ("SWA", "Southwest Airlines", "Travel"),
    ("DELTA AIR", "Delta", "Travel"),
    ("FRONTIER", "Frontier Airlines", "Travel"),
    ("AMERICAN AIR", "American Airlines", "Travel"),
    ("ALASKA AIR", "Alaska Airlines", "Travel"),
    ("AVIS", "Avis", "Travel"),
    ("HERTZ", "Hertz", "Travel"),
    ("ENTERPRISE RENT", "Enterprise", "Travel"),
    ("TURO", "Turo", "Travel"),
    ("EXPEDIA", "Expedia", "Travel"),
    ("AIRBNB", "Airbnb", "Travel"),
    ("VRBO", "Vrbo", "Travel"),
    ("MARRIOTT", "Marriott", "Travel"),
    ("HILTON", "Hilton", "Travel"),
    ("HOLIDAY INN", "Holiday Inn", "Travel"),
    ("UBER", "Uber", "Travel"),
    ("LYFT", "Lyft", "Travel"),
]

# (keyword found in the *clean* name, category) - e.g. anything named "... Gas" is Auto & Gas.
NAME_RULES = [
    ("Gas", "Auto & Gas"),
    ("Fuel", "Auto & Gas"),
    ("Toll", "Auto & Gas"),
    ("Tolls", "Auto & Gas"),
    ("Car Wash", "Auto & Gas"),
    ("Car Payment", "Auto & Gas"),
    ("Car", "Auto & Gas"),
    ("Truck", "Auto & Gas"),
    ("Automotive", "Auto & Gas"),
    ("Auto", "Auto & Gas"),
    ("DMV", "Auto & Gas"),
    ("Parking", "Auto & Gas"),
    ("Pizza", "Dining & Drinks"),
    ("Pizzeria", "Dining & Drinks"),
    ("Burger", "Dining & Drinks"),
    ("Burgers", "Dining & Drinks"),
    ("Tacos", "Dining & Drinks"),
    ("Sushi", "Dining & Drinks"),
    ("Cafe", "Dining & Drinks"),
    ("Café", "Dining & Drinks"),
    ("Coffee", "Dining & Drinks"),
    ("Diner", "Dining & Drinks"),
    ("Grill", "Dining & Drinks"),
    ("Deli", "Dining & Drinks"),
    ("Bagels", "Dining & Drinks"),
    ("BBQ", "Dining & Drinks"),
    ("Food Court", "Dining & Drinks"),
    ("Yogurtland", "Dining & Drinks"),
    ("BJ's", "Dining & Drinks"),
    ("Brewing", "Dining & Drinks"),
    ("Brewery", "Dining & Drinks"),
    ("Tavern", "Dining & Drinks"),
    ("Saloon", "Dining & Drinks"),
    ("Tap House", "Dining & Drinks"),
    ("Liquor", "Dining & Drinks"),
    ("Liquors", "Dining & Drinks"),
    ("Wine", "Dining & Drinks"),
    ("Spirits", "Dining & Drinks"),
    ("Bar", "Dining & Drinks"),
    ("Vet", "Pets"),
    ("Puppy", "Pets"),
    ("Dog", "Pets"),
    ("Hotel", "Travel"),
    ("Motel", "Travel"),
    ("Lodge", "Travel"),
    ("Resort", "Travel"),
    ("Airlines", "Travel"),
    ("Hair", "Shopping & Personal"),
    ("Haircut", "Shopping & Personal"),
    ("Nails", "Shopping & Personal"),
    ("Cleaners", "Shopping & Personal"),
    ("Dentist", "Health & Fitness"),
    ("Dental", "Health & Fitness"),
    ("Ortho", "Health & Fitness"),
    ("Chiro", "Health & Fitness"),
    ("Pharmacy", "Health & Fitness"),
    ("Massage", "Health & Fitness"),
    ("Health", "Health & Fitness"),
    ("Fitness", "Health & Fitness"),
    ("Bowling", "Hobbies & Fun"),
    ("Lanes", "Hobbies & Fun"),
    ("Golf", "Hobbies & Fun"),
    ("AXS", "Hobbies & Fun"),
    ("Donation", "Giving"),
    ("Wedding", "Other"),
    ("Tuition", "Kids & Education"),
    ("Textbook", "Kids & Education"),
    ("Bookstore", "Kids & Education"),
    ("Mortgage", "Mortgage & HOA"),
    ("HOA", "Mortgage & HOA"),
    ("Escrow", "Mortgage & HOA"),
    ("Property Tax", "Taxes & Fees"),
    ("Property Taxes", "Taxes & Fees"),
    ("Remodel", "Home & Garden"),
    ("Insurance", "Bills & Utilities"),
    ("State Farm", "Bills & Utilities"),
    ("Student Loans", "Kids & Education"),
    ("Income", "Paycheck"),
    ("Paycheck", "Paycheck"),
    ("Bonus", "Paycheck"),
    ("Rent", "Rental Income"),
    ("Rent Deposit", "Rental Income"),
    ("Security Deposit", "Other Income"),
    ("Interest", "Other Income"),
    ("Interest Paid", "Other Income"),
    ("Refund", "Other Income"),
    ("Rebate", "Other Income"),
    ("Tax Return", "Other Income"),
    ("Tax Refund", "Other Income"),
    ("Reimburse", "Other Income"),
    ("Reimbursement", "Other Income"),
    ("Reward", "Other Income"),
    ("Rewards", "Other Income"),
    ("Transfer", "Transfer"),
    ("HYSA", "Transfer"),
    ("Down Payment", "Transfer"),
    ("Chase Card", "Transfer"),
    ("Merrill", "Savings & Investments"),
    ("Merrill Lynch", "Savings & Investments"),
    ("Fidelity", "Savings & Investments"),
    ("Coinbase", "Savings & Investments"),
    ("ATM", "Other"),
    ("Cash Withdraw", "Other"),
    ("ATM Fee", "Taxes & Fees"),
    ("Fee", "Taxes & Fees"),
    ("Check Deposit", "Other Income"),
    ("Xmas", "Other Income"),
    ("Christmas", "Other Income"),
    ("Gift", "Giving"),
    ("Subscription", "Bills & Utilities"),
    ("Google", "Bills & Utilities"),
    ("Microsoft", "Bills & Utilities"),
    ("Water", "Bills & Utilities"),
]

MCC_CATEGORY = {
    **dict.fromkeys((5411, 5422, 5441, 5451, 5462, 5499, 5300), "Groceries"),
    **dict.fromkeys((5541, 5542, 5531, 5532, 5533, 7531, 7534, 7535, 7538, 7542, 7549, 4784, 7523, 5511, 5521), "Auto & Gas"),
    **dict.fromkeys((5811, 5812, 5814), "Dining & Drinks"),
    **dict.fromkeys((5813, 5921), "Dining & Drinks"),
    **dict.fromkeys((4814, 4900), "Bills & Utilities"),
    **dict.fromkeys((4899, 5815, 5816, 5817, 5818), "Bills & Utilities"),
    **dict.fromkeys((5200, 5211, 5231, 5251, 5261, 5712, 5713, 5714, 5718, 5719, 5722, 7342, 7349, 780), "Home & Garden"),
    **dict.fromkeys((5611, 5621, 5631, 5641, 5651, 5661, 5681, 5691, 5697, 5698, 5699, 7210, 7211, 7216, 7230, 7297, 7298), "Shopping & Personal"),
    **dict.fromkeys((5912, 5122, 5975, 5976, 8011, 8021, 8031, 8041, 8042, 8043, 8049, 8050, 8062, 8071, 8099), "Health & Fitness"),
    **dict.fromkeys((7997,), "Health & Fitness"),
    **dict.fromkeys((5655, 5940, 5941), "Hobbies & Fun"),
    **dict.fromkeys((7832, 7841, 7922, 7929, 7932, 7933, 7941, 7991, 7992, 7993, 7994, 7996, 7998, 7999), "Hobbies & Fun"),
    **dict.fromkeys((5732, 5733, 5734, 5735, 5310, 5311, 5331, 5399, 5931, 5932, 5942, 5943, 5944, 5945, 5946, 5947, 5948, 5949, 5970, 5977, 5992, 5993, 5994, 5999), "Shopping & Personal"),
    **dict.fromkeys((8398, 8661), "Giving"),
    **dict.fromkeys((8211, 8220, 8241, 8244, 8249, 8299), "Kids & Education"),
    **dict.fromkeys((8351,), "Kids & Education"),
    **dict.fromkeys((742, 5995), "Pets"),
    **dict.fromkeys((6300, 5960), "Bills & Utilities"),
    **dict.fromkeys((9211, 9222, 9311, 9399), "Taxes & Fees"),
    **dict.fromkeys((4111, 4121, 4131, 4411, 4511, 4722, 4789, 7011, 7012, 7512, 7513, 7519), "Travel"),
    **dict.fromkeys((6010, 6011), "Other"),
}

# (kind, label, asset/liability, group shown on the net worth page)
ACCOUNT_KINDS = [
    ("checking", "Checking", "asset", "Cash"),
    ("savings", "Savings", "asset", "Cash"),
    ("cash", "Cash", "asset", "Cash"),
    ("brokerage", "Brokerage", "asset", "Investments"),
    ("retirement", "Retirement", "asset", "Investments"),
    ("hsa", "HSA or FSA", "asset", "Investments"),
    ("crypto", "Crypto", "asset", "Investments"),
    ("property", "Real estate", "asset", "Property & other"),
    ("vehicle", "Vehicle", "asset", "Property & other"),
    ("other", "Other asset", "asset", "Property & other"),
    # Money you track but isn't yours to spend: a child's 529, a custodial account. Net worth leaves
    # it out of your totals unless you tick it.
    ("held", "Held for someone else (529, custodial)", "asset", "Held for others"),
    ("credit", "Credit card", "liability", "Debts"),
    ("loan", "Loan or mortgage", "liability", "Debts"),
    ("other_debt", "Other debt", "liability", "Debts"),
]
ACCOUNT_GROUPS = ["Cash", "Investments", "Property & other", "Held for others", "Debts"]
HELD_GROUP = "Held for others"
KIND = {key: {"label": label, "side": side, "group": group} for key, label, side, group in ACCOUNT_KINDS}


def account_side(kind):
    return KIND.get(kind, KIND["other"])["side"]


def guess_kind(name, side="asset"):
    """Best guess at an account type from its name (used when upgrading old net worth items)."""
    n = name.lower()
    guesses = [
        (("credit",), "credit"),
        (("checking",), "checking"),
        (("saving", "hysa"), "savings"),
        (("401k", "ira", "voya", "retire"), "retirement"),
        (("hsa", "fsa"), "hsa"),
        (("coinbase", "crypto"), "crypto"),
        (("529", "custodial", "utma", "ugma"), "held"),
        (("merrill", "schwab", "fidelity", "vanguard", "brokerage"), "brokerage"),
    ]
    for words, kind in guesses:
        if any(w in n for w in words):
            return kind
    if side == "liability":
        return "loan"
    if "home" in n or "house" in n:
        return "property"
    if "vehicle" in n or "car" in n:
        return "vehicle"
    return "other"


def mcc_category(mcc):
    """Category name for a merchant category code, or None."""
    try:
        code = int(mcc)
    except (TypeError, ValueError):
        return None
    if 3000 <= code <= 3299 or 3351 <= code <= 3441 or 3500 <= code <= 3999:
        return "Travel"  # airlines, car rental, hotels
    return category_name(MCC_CATEGORY.get(code))


def _personal_rule(conn, match_on, pattern, rename_to, category_id, amount=None):
    """Add or refresh a rule from personal.toml. Returns 1 if anything changed.

    It replaces built-in rules and its own earlier version, but never a rule you edited in the
    app or a name learned from your spreadsheet.
    """
    row = conn.execute(
        "SELECT id, source, rename_to, category_id FROM rules WHERE match_on = ? AND pattern = ? AND amount IS ?",
        (match_on, pattern, amount),
    ).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO rules (match_on, pattern, rename_to, category_id, source, amount)
               VALUES (?, ?, ?, ?, 'config', ?)""",
            (match_on, pattern, rename_to, category_id, amount),
        )
        return 1
    if row["source"] not in ("builtin", "config"):
        return 0  # you edited it in the app, or it came from your spreadsheet
    if row["source"] == "config" and row["rename_to"] == rename_to and row["category_id"] == category_id:
        return 0
    conn.execute(
        "UPDATE rules SET rename_to = ?, category_id = ?, source = 'config' WHERE id = ?",
        (rename_to, category_id, row["id"]),
    )
    return 1


def seed_defaults(conn, personal=None):
    """Load built-in and personal categories/rules. Returns how many personal rules changed."""
    extra = list(personal.categories) if personal else []
    for sort, (name, kind) in enumerate(CATEGORIES + [(category_name(n), k) for n, k in extra]):
        conn.execute(
            "INSERT OR IGNORE INTO categories (name, kind, sort, snap_to_month, grp) VALUES (?, ?, ?, ?, ?)",
            (name, kind, sort, int(name in SNAP_TO_MONTH), category_group(name, kind)),
        )
    cat_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
    for pattern, rename_to, category in BUILTIN_RULES:
        conn.execute(
            "INSERT OR IGNORE INTO rules (match_on, pattern, rename_to, category_id, source) VALUES ('raw', ?, ?, ?, 'builtin')",
            (pattern, rename_to, cat_ids.get(category_name(category))),
        )
    for pattern, category in NAME_RULES:
        conn.execute(
            "INSERT OR IGNORE INTO rules (match_on, pattern, category_id, source) VALUES ('name', ?, ?, 'builtin')",
            (pattern, cat_ids.get(category_name(category))),
        )
    if not personal:
        return 0

    def category_id(name, entry):
        if name is None:
            return None
        name = category_name(name)
        if name not in cat_ids:
            print(f"personal.toml: {entry} uses unknown category {name!r}. Add it under [[category]] or fix the name.",
                  file=sys.stderr)
        return cat_ids.get(name)

    changed = 0
    for match, rename_to, category, amount in personal.merchants:
        pattern = match if match.startswith("re:") else " ".join(match.upper().split())
        changed += _personal_rule(
            conn, "raw", pattern, rename_to, category_id(category, f"merchant {match!r}"), amount
        )
    for keyword, category in personal.keywords:
        changed += _personal_rule(conn, "name", keyword, None, category_id(category, f"keyword {keyword!r}"))
    return changed
