"""Built-in categories, merchant dictionary, and merchant-category-code (MCC) fallbacks.

Only general, nationally known merchants live here. Household-specific entries (local
businesses, extra categories, keywords) go in data/personal.toml; see personal.example.toml.

Everything is loaded with INSERT OR IGNORE, so edits made in the app are never overwritten,
and new built-in entries added here show up on the next start.
"""
import sys

CATEGORIES = [
    # Income
    ("Paycheck", "income"),
    ("Rental Income", "income"),
    ("Side Income", "income"),
    ("Refunds & Reimbursements", "income"),
    ("Interest & Dividends", "income"),
    ("Gifts Received", "income"),
    ("Other Income", "income"),
    # Spending
    ("Mortgage & HOA", "expense"),
    ("Utilities & Internet", "expense"),
    ("Home & Garden", "expense"),
    ("Groceries", "expense"),
    ("Eating Out", "expense"),
    ("Drinks & Bars", "expense"),
    ("Auto & Gas", "expense"),
    ("Fitness", "expense"),
    ("Outdoor & Hunting", "expense"),
    ("Clothes & Hygiene", "expense"),
    ("Health & Medical", "expense"),
    ("Kids & School", "expense"),
    ("Pets", "expense"),
    ("Donations", "expense"),
    ("Travel", "expense"),
    ("Entertainment", "expense"),
    ("Shopping", "expense"),
    ("Subscriptions", "expense"),
    ("Insurance", "expense"),
    ("Education & Tuition", "expense"),
    ("Student Loans", "expense"),
    ("Wedding", "expense"),
    ("Gifts Given", "expense"),
    ("Taxes & Fees", "expense"),
    ("Cash & ATM", "expense"),
    ("Other", "expense"),
    # Money moving between your own accounts (left out of spending/income totals)
    ("Credit Card Payment", "transfer"),
    ("Transfer", "transfer"),
    ("Savings & Investments", "transfer"),
]

# (pattern found in the bank's raw description, clean name, category)
BUILTIN_RULES = [
    # Card payments, transfers, payroll
    ("PAYMENT THANK YOU", "Credit Card Payment", "Credit Card Payment"),
    ("INTERNET PAYMENT", "Credit Card Payment", "Credit Card Payment"),
    ("CARDMEMBER SERV", "US Bank Card Payment", "Credit Card Payment"),
    ("CHASE CREDIT CRD", "Chase Card Payment", "Credit Card Payment"),
    ("AUTOPAY PAYMENT", "Credit Card Payment", "Credit Card Payment"),
    ("MOBILE BANKING TRANSFER", "Transfer", "Transfer"),
    ("ONLINE TRANSFER", "Transfer", "Transfer"),
    ("INTERNET BANKING TRANSFER", "Transfer", "Transfer"),
    ("PAYROLL", "Paycheck", "Paycheck"),
    ("DIRECT DEP", "Paycheck", "Paycheck"),
    ("INTEREST PAID", "Interest", "Interest & Dividends"),
    ("ANNUAL MEMBERSHIP FEE", "Annual Card Fee", "Taxes & Fees"),
    ("ATM WITHDRAWAL", "ATM Withdrawal", "Cash & ATM"),
    ("ZELLE", "Zelle", None),
    ("VENMO", "Venmo", None),
    ("MERRILL", "Merrill", "Savings & Investments"),
    ("ML ", "Merrill", "Savings & Investments"),
    ("SCHWAB", "Charles Schwab", "Savings & Investments"),
    ("VOYA", "Voya", "Savings & Investments"),
    ("FIDELITY", "Fidelity", "Savings & Investments"),
    ("VANGUARD", "Vanguard", "Savings & Investments"),
    ("COINBASE", "Coinbase", "Savings & Investments"),
    ("MOHELA", "Mohela Student Loans", "Student Loans"),
    ("NAVIENT", "Navient Student Loans", "Student Loans"),
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
    ("WALMART", "Walmart", "Shopping"),
    ("WAL-MART", "Walmart", "Shopping"),
    ("WM SUPERCENTER", "Walmart", "Shopping"),
    ("TARGET", "Target", "Shopping"),
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
    ("BUFFALO WILD", "Buffalo Wild Wings", "Eating Out"),
    ("CHIPOTLE", "Chipotle", "Eating Out"),
    ("MCDONALD'S", "McDonald's", "Eating Out"),
    ("TACO BELL", "Taco Bell", "Eating Out"),
    ("WENDYS", "Wendy's", "Eating Out"),
    ("CHICK-FIL-A", "Chick-fil-A", "Eating Out"),
    ("JIMMY JOHNS", "Jimmy John's", "Eating Out"),
    ("SUBWAY", "Subway", "Eating Out"),
    ("STARBUCKS", "Starbucks", "Eating Out"),
    ("DUTCH BROS", "Dutch Bros", "Eating Out"),
    ("DOMINOS", "Domino's", "Eating Out"),
    ("PIZZA HUT", "Pizza Hut", "Eating Out"),
    ("PAPA JOHN", "Papa John's", "Eating Out"),
    ("MARCOS PIZZA", "Marco's Pizza", "Eating Out"),
    ("PANERA", "Panera", "Eating Out"),
    ("QDOBA", "Qdoba", "Eating Out"),
    ("POTBELLY", "Potbelly", "Eating Out"),
    ("NOODLES", "Noodles & Co", "Eating Out"),
    ("SONIC DRIVE", "Sonic", "Eating Out"),
    ("BURGER KING", "Burger King", "Eating Out"),
    ("ARBYS", "Arby's", "Eating Out"),
    ("DAIRY QUEEN", "Dairy Queen", "Eating Out"),
    ("DENNY'S", "Denny's", "Eating Out"),
    ("IHOP", "IHOP", "Eating Out"),
    ("CULVERS", "Culver's", "Eating Out"),
    ("IN-N-OUT", "In-N-Out", "Eating Out"),
    ("FIVE GUYS", "Five Guys", "Eating Out"),
    ("RAISING CANES", "Raising Cane's", "Eating Out"),
    ("PANDA EXPRESS", "Panda Express", "Eating Out"),
    ("RED ROBIN", "Red Robin", "Eating Out"),
    ("MOD PIZZA", "MOD Pizza", "Eating Out"),
    ("FIREHOUSE SUBS", "Firehouse Subs", "Eating Out"),
    ("JERSEY MIKES", "Jersey Mike's", "Eating Out"),
    ("KRISPY KREME", "Krispy Kreme", "Eating Out"),
    ("DOORDASH", "DoorDash", "Eating Out"),
    ("UBER EATS", "Uber Eats", "Eating Out"),
    ("GRUBHUB", "Grubhub", "Eating Out"),
    # Drinks
    ("LIQUOR", None, "Drinks & Bars"),
    # Shopping
    ("AMAZON MKTPL", "Amazon", "Shopping"),
    ("AMAZON MKTPLACE PMTS", "Amazon Refund", "Refunds & Reimbursements"),
    ("AMZN MKTP", "Amazon", "Shopping"),
    ("AMAZON.COM", "Amazon", "Shopping"),
    ("AMAZON RETA", "Amazon", "Shopping"),
    ("AMAZON RET", "Amazon", "Shopping"),
    ("AMAZON PRIME", "Amazon Prime", "Subscriptions"),
    ("PRIME VIDEO", "Prime Video", "Subscriptions"),
    ("AMAZON KIDS", "Amazon Kids+", "Subscriptions"),
    ("BEST BUY", "Best Buy", "Shopping"),
    ("HOBBY-LOBBY", "Hobby Lobby", "Shopping"),
    ("GOODWILL", "Goodwill", "Shopping"),
    ("ETSY", "Etsy", "Shopping"),
    ("EBAY", "eBay", "Shopping"),
    ("KOHLS", "Kohl's", "Clothes & Hygiene"),
    ("TJ MAXX", "TJ Maxx", "Clothes & Hygiene"),
    ("ROSS STORES", "Ross", "Clothes & Hygiene"),
    ("OLD NAVY", "Old Navy", "Clothes & Hygiene"),
    ("BUCKLE", "Buckle", "Clothes & Hygiene"),
    ("NORDSTROM", "Nordstrom", "Clothes & Hygiene"),
    ("EUROPEAN WAX", "European Wax Center", "Clothes & Hygiene"),
    ("HOME DEPOT", "Home Depot", "Home & Garden"),
    ("LOWES", "Lowe's", "Home & Garden"),
    ("ACE HARDWA", "Ace Hardware", "Home & Garden"),
    ("IKEA", "IKEA", "Home & Garden"),
    ("ASHLEYFURNITURE", "Ashley Furniture", "Home & Garden"),
    ("TRUGREEN", "TruGreen", "Home & Garden"),
    ("MOLLY MAID", "Molly Maid", "Home & Garden"),
    # Outdoor & fitness
    ("CABELAS", "Cabela's", "Outdoor & Hunting"),
    ("CAB STORE", "Cabela's", "Outdoor & Hunting"),
    ("SCHEELS", "Scheels", "Outdoor & Hunting"),
    ("SPORTSMANS WAREHOUSE", "Sportsman's Warehouse", "Outdoor & Hunting"),
    ("BASS PRO", "Bass Pro Shops", "Outdoor & Hunting"),
    ("REI", "REI", "Outdoor & Hunting"),
    ("PLANET FITNESS", "Planet Fitness", "Fitness"),
    ("LIFE TIME", "Life Time", "Fitness"),
    ("ORANGETHEORY", "Orangetheory", "Fitness"),
    ("GNC", "GNC", "Fitness"),
    # Entertainment & subscriptions
    ("BOWLERO", "Bowlero", "Entertainment"),
    ("AMC", "AMC Theatres", "Entertainment"),
    ("REGAL", "Regal Cinemas", "Entertainment"),
    ("ALAMO DRAFTHOUSE", "Alamo Drafthouse", "Entertainment"),
    ("TICKETMASTER", "Ticketmaster", "Entertainment"),
    ("AXS.COM", "AXS Tickets", "Entertainment"),
    ("STUBHUB", "StubHub", "Entertainment"),
    ("SEATGEEK", "SeatGeek", "Entertainment"),
    ("TOPGOLF", "Topgolf", "Entertainment"),
    ("SPOTIFY", "Spotify", "Subscriptions"),
    ("NETFLIX", "Netflix", "Subscriptions"),
    ("HULU", "Hulu", "Subscriptions"),
    ("DISNEYPLUS", "Disney+", "Subscriptions"),
    ("DISNEY PLUS", "Disney+", "Subscriptions"),
    ("GOOGLE STORAGE", "Google One", "Subscriptions"),
    ("GOOGLE ONE", "Google One", "Subscriptions"),
    ("YOUTUBE", "YouTube", "Subscriptions"),
    ("APPLE.COM/BILL", "Apple", "Subscriptions"),
    ("HBO MAX", "Max", "Subscriptions"),
    ("PARAMOUNT", "Paramount+", "Subscriptions"),
    ("PEACOCK", "Peacock", "Subscriptions"),
    ("AUDIBLE", "Audible", "Subscriptions"),
    ("GARMIN", "Garmin", "Subscriptions"),
    # House, utilities, insurance
    ("CENTURYLINK", "CenturyLink", "Utilities & Internet"),
    ("QUANTUM FIBER", "Quantum Fiber", "Utilities & Internet"),
    ("XCEL ENERGY", "Xcel Energy", "Utilities & Internet"),
    ("COMCAST", "Xfinity", "Utilities & Internet"),
    ("XFINITY", "Xfinity", "Utilities & Internet"),
    ("VERIZON", "Verizon", "Utilities & Internet"),
    ("T-MOBILE", "T-Mobile", "Utilities & Internet"),
    ("AT&T", "AT&T", "Utilities & Internet"),
    ("ADT SECURITY", "ADT", "Utilities & Internet"),
    ("STATE FARM", "State Farm", "Insurance"),
    ("GEICO", "GEICO", "Insurance"),
    ("PROGRESSIVE", "Progressive", "Insurance"),
    ("ALLSTATE", "Allstate", "Insurance"),
    # Health, pets, kids
    ("CVS/PHARMACY", "CVS", "Health & Medical"),
    ("WALGREENS", "Walgreens", "Health & Medical"),
    ("HAND AND STONE", "Hand & Stone Massage", "Health & Medical"),
    ("THE JOINT CHIROPRACTIC", "The Joint Chiropractic", "Health & Medical"),
    ("CHEWY", "Chewy", "Pets"),
    ("PETSMART", "PetSmart", "Pets"),
    ("PETCO", "Petco", "Pets"),
    ("SCHOLASTIC", "Scholastic", "Kids & School"),
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
    ("Pizza", "Eating Out"),
    ("Pizzeria", "Eating Out"),
    ("Burger", "Eating Out"),
    ("Burgers", "Eating Out"),
    ("Tacos", "Eating Out"),
    ("Sushi", "Eating Out"),
    ("Cafe", "Eating Out"),
    ("Café", "Eating Out"),
    ("Coffee", "Eating Out"),
    ("Diner", "Eating Out"),
    ("Grill", "Eating Out"),
    ("Deli", "Eating Out"),
    ("Bagels", "Eating Out"),
    ("BBQ", "Eating Out"),
    ("Food Court", "Eating Out"),
    ("Yogurtland", "Eating Out"),
    ("BJ's", "Eating Out"),
    ("Brewing", "Drinks & Bars"),
    ("Brewery", "Drinks & Bars"),
    ("Tavern", "Drinks & Bars"),
    ("Saloon", "Drinks & Bars"),
    ("Tap House", "Drinks & Bars"),
    ("Liquor", "Drinks & Bars"),
    ("Liquors", "Drinks & Bars"),
    ("Wine", "Drinks & Bars"),
    ("Spirits", "Drinks & Bars"),
    ("Bar", "Drinks & Bars"),
    ("Vet", "Pets"),
    ("Puppy", "Pets"),
    ("Dog", "Pets"),
    ("Hotel", "Travel"),
    ("Motel", "Travel"),
    ("Lodge", "Travel"),
    ("Resort", "Travel"),
    ("Airlines", "Travel"),
    ("Hair", "Clothes & Hygiene"),
    ("Haircut", "Clothes & Hygiene"),
    ("Nails", "Clothes & Hygiene"),
    ("Cleaners", "Clothes & Hygiene"),
    ("Dentist", "Health & Medical"),
    ("Dental", "Health & Medical"),
    ("Ortho", "Health & Medical"),
    ("Chiro", "Health & Medical"),
    ("Pharmacy", "Health & Medical"),
    ("Massage", "Health & Medical"),
    ("Health", "Health & Medical"),
    ("Fitness", "Fitness"),
    ("Bowling", "Entertainment"),
    ("Lanes", "Entertainment"),
    ("Golf", "Entertainment"),
    ("AXS", "Entertainment"),
    ("Donation", "Donations"),
    ("Wedding", "Wedding"),
    ("Tuition", "Education & Tuition"),
    ("Textbook", "Education & Tuition"),
    ("Bookstore", "Education & Tuition"),
    ("Mortgage", "Mortgage & HOA"),
    ("HOA", "Mortgage & HOA"),
    ("Escrow", "Mortgage & HOA"),
    ("Property Tax", "Taxes & Fees"),
    ("Property Taxes", "Taxes & Fees"),
    ("Remodel", "Home & Garden"),
    ("Insurance", "Insurance"),
    ("State Farm", "Insurance"),
    ("Student Loans", "Student Loans"),
    ("Income", "Paycheck"),
    ("Paycheck", "Paycheck"),
    ("Bonus", "Paycheck"),
    ("Rent", "Rental Income"),
    ("Rent Deposit", "Rental Income"),
    ("Security Deposit", "Other Income"),
    ("Interest", "Interest & Dividends"),
    ("Interest Paid", "Interest & Dividends"),
    ("Refund", "Refunds & Reimbursements"),
    ("Rebate", "Refunds & Reimbursements"),
    ("Tax Return", "Refunds & Reimbursements"),
    ("Tax Refund", "Refunds & Reimbursements"),
    ("Reimburse", "Refunds & Reimbursements"),
    ("Reimbursement", "Refunds & Reimbursements"),
    ("Reward", "Refunds & Reimbursements"),
    ("Rewards", "Refunds & Reimbursements"),
    ("Transfer", "Transfer"),
    ("HYSA", "Transfer"),
    ("Down Payment", "Transfer"),
    ("Chase Card", "Credit Card Payment"),
    ("Merrill", "Savings & Investments"),
    ("Merrill Lynch", "Savings & Investments"),
    ("Fidelity", "Savings & Investments"),
    ("Coinbase", "Savings & Investments"),
    ("ATM", "Cash & ATM"),
    ("Cash Withdraw", "Cash & ATM"),
    ("ATM Fee", "Taxes & Fees"),
    ("Fee", "Taxes & Fees"),
    ("Check Deposit", "Other Income"),
    ("Xmas", "Gifts Received"),
    ("Christmas", "Gifts Received"),
    ("Gift", "Gifts Given"),
    ("Subscription", "Subscriptions"),
    ("Google", "Subscriptions"),
    ("Microsoft", "Subscriptions"),
    ("Water", "Utilities & Internet"),
]

MCC_CATEGORY = {
    **dict.fromkeys((5411, 5422, 5441, 5451, 5462, 5499, 5300), "Groceries"),
    **dict.fromkeys((5541, 5542, 5531, 5532, 5533, 7531, 7534, 7535, 7538, 7542, 7549, 4784, 7523, 5511, 5521), "Auto & Gas"),
    **dict.fromkeys((5811, 5812, 5814), "Eating Out"),
    **dict.fromkeys((5813, 5921), "Drinks & Bars"),
    **dict.fromkeys((4814, 4900), "Utilities & Internet"),
    **dict.fromkeys((4899, 5815, 5816, 5817, 5818), "Subscriptions"),
    **dict.fromkeys((5200, 5211, 5231, 5251, 5261, 5712, 5713, 5714, 5718, 5719, 5722, 7342, 7349, 780), "Home & Garden"),
    **dict.fromkeys((5611, 5621, 5631, 5641, 5651, 5661, 5681, 5691, 5697, 5698, 5699, 7210, 7211, 7216, 7230, 7297, 7298), "Clothes & Hygiene"),
    **dict.fromkeys((5912, 5122, 5975, 5976, 8011, 8021, 8031, 8041, 8042, 8043, 8049, 8050, 8062, 8071, 8099), "Health & Medical"),
    **dict.fromkeys((7997,), "Fitness"),
    **dict.fromkeys((5655, 5940, 5941), "Outdoor & Hunting"),
    **dict.fromkeys((7832, 7841, 7922, 7929, 7932, 7933, 7941, 7991, 7992, 7993, 7994, 7996, 7998, 7999), "Entertainment"),
    **dict.fromkeys((5732, 5733, 5734, 5735, 5310, 5311, 5331, 5399, 5931, 5932, 5942, 5943, 5944, 5945, 5946, 5947, 5948, 5949, 5970, 5977, 5992, 5993, 5994, 5999), "Shopping"),
    **dict.fromkeys((8398, 8661), "Donations"),
    **dict.fromkeys((8211, 8220, 8241, 8244, 8249, 8299), "Education & Tuition"),
    **dict.fromkeys((8351,), "Kids & School"),
    **dict.fromkeys((742, 5995), "Pets"),
    **dict.fromkeys((6300, 5960), "Insurance"),
    **dict.fromkeys((9211, 9222, 9311, 9399), "Taxes & Fees"),
    **dict.fromkeys((4111, 4121, 4131, 4411, 4511, 4722, 4789, 7011, 7012, 7512, 7513, 7519), "Travel"),
    **dict.fromkeys((6010, 6011), "Cash & ATM"),
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
    ("credit", "Credit card", "liability", "Debts"),
    ("loan", "Loan or mortgage", "liability", "Debts"),
    ("other_debt", "Other debt", "liability", "Debts"),
]
ACCOUNT_GROUPS = ["Cash", "Investments", "Property & other", "Debts"]
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
    return MCC_CATEGORY.get(code)


def _personal_rule(conn, match_on, pattern, rename_to, category_id):
    """Add or refresh a rule from personal.toml. Returns 1 if anything changed.

    It replaces built-in rules and its own earlier version, but never a rule you edited in the
    app or a name learned from your spreadsheet.
    """
    return conn.execute(
        """INSERT INTO rules (match_on, pattern, rename_to, category_id, source) VALUES (?, ?, ?, ?, 'config')
           ON CONFLICT (match_on, pattern) DO UPDATE
             SET rename_to = excluded.rename_to, category_id = excluded.category_id, source = 'config'
           WHERE rules.source IN ('builtin', 'config')
             AND (rules.source != 'config' OR rules.rename_to IS NOT excluded.rename_to
                  OR rules.category_id IS NOT excluded.category_id)""",
        (match_on, pattern, rename_to, category_id),
    ).rowcount


def seed_defaults(conn, personal=None):
    """Load built-in and personal categories/rules. Returns how many personal rules changed."""
    extra = list(personal.categories) if personal else []
    for sort, (name, kind) in enumerate(CATEGORIES + extra):
        conn.execute("INSERT OR IGNORE INTO categories (name, kind, sort) VALUES (?, ?, ?)", (name, kind, sort))
    cat_ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
    for pattern, rename_to, category in BUILTIN_RULES:
        conn.execute(
            "INSERT OR IGNORE INTO rules (match_on, pattern, rename_to, category_id, source) VALUES ('raw', ?, ?, ?, 'builtin')",
            (pattern, rename_to, cat_ids.get(category)),
        )
    for pattern, category in NAME_RULES:
        conn.execute(
            "INSERT OR IGNORE INTO rules (match_on, pattern, category_id, source) VALUES ('name', ?, ?, 'builtin')",
            (pattern, cat_ids.get(category)),
        )
    if not personal:
        return 0

    def category_id(name, entry):
        if name is None:
            return None
        if name not in cat_ids:
            print(f"personal.toml: {entry} uses unknown category {name!r}. Add it under [[category]] or fix the name.",
                  file=sys.stderr)
        return cat_ids.get(name)

    changed = 0
    for match, rename_to, category in personal.merchants:
        pattern = match if match.startswith("re:") else " ".join(match.upper().split())
        changed += _personal_rule(conn, "raw", pattern, rename_to, category_id(category, f"merchant {match!r}"))
    for keyword, category in personal.keywords:
        changed += _personal_rule(conn, "name", keyword, None, category_id(category, f"keyword {keyword!r}"))
    return changed
