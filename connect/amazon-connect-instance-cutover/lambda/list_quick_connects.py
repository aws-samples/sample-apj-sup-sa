import json
from region_utils import create_connect_client_from_arn
from datetime import datetime
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def extract_phone_prefix(phone_number):
    """Extract country code prefix from phone number (e.g., +1, +44, +91)"""
    if not phone_number or not phone_number.startswith('+'):
        return None

    # Common country code lengths: 1-3 digits after +
    # Map of known country codes for better extraction
    country_codes = {
        '1': '+1',      # USA, Canada
        '7': '+7',      # Russia, Kazakhstan
        '20': '+20',    # Egypt
        '27': '+27',    # South Africa
        '30': '+30',    # Greece
        '31': '+31',    # Netherlands
        '32': '+32',    # Belgium
        '33': '+33',    # France
        '34': '+34',    # Spain
        '36': '+36',    # Hungary
        '39': '+39',    # Italy
        '40': '+40',    # Romania
        '41': '+41',    # Switzerland
        '43': '+43',    # Austria
        '44': '+44',    # UK
        '45': '+45',    # Denmark
        '46': '+46',    # Sweden
        '47': '+47',    # Norway
        '48': '+48',    # Poland
        '49': '+49',    # Germany
        '51': '+51',    # Peru
        '52': '+52',    # Mexico
        '53': '+53',    # Cuba
        '54': '+54',    # Argentina
        '55': '+55',    # Brazil
        '56': '+56',    # Chile
        '57': '+57',    # Colombia
        '58': '+58',    # Venezuela
        '60': '+60',    # Malaysia
        '61': '+61',    # Australia
        '62': '+62',    # Indonesia
        '63': '+63',    # Philippines
        '64': '+64',    # New Zealand
        '65': '+65',    # Singapore
        '66': '+66',    # Thailand
        '81': '+81',    # Japan
        '82': '+82',    # South Korea
        '84': '+84',    # Vietnam
        '86': '+86',    # China
        '90': '+90',    # Turkey
        '91': '+91',    # India
        '92': '+92',    # Pakistan
        '93': '+93',    # Afghanistan
        '94': '+94',    # Sri Lanka
        '95': '+95',    # Myanmar
        '98': '+98',    # Iran
        '212': '+212',  # Morocco
        '213': '+213',  # Algeria
        '216': '+216',  # Tunisia
        '218': '+218',  # Libya
        '220': '+220',  # Gambia
        '221': '+221',  # Senegal
        '234': '+234',  # Nigeria
        '254': '+254',  # Kenya
        '255': '+255',  # Tanzania
        '256': '+256',  # Uganda
        '260': '+260',  # Zambia
        '263': '+263',  # Zimbabwe
        '351': '+351',  # Portugal
        '352': '+352',  # Luxembourg
        '353': '+353',  # Ireland
        '354': '+354',  # Iceland
        '355': '+355',  # Albania
        '356': '+356',  # Malta
        '357': '+357',  # Cyprus
        '358': '+358',  # Finland
        '359': '+359',  # Bulgaria
        '370': '+370',  # Lithuania
        '371': '+371',  # Latvia
        '372': '+372',  # Estonia
        '380': '+380',  # Ukraine
        '381': '+381',  # Serbia
        '385': '+385',  # Croatia
        '386': '+386',  # Slovenia
        '420': '+420',  # Czech Republic
        '421': '+421',  # Slovakia
        '852': '+852',  # Hong Kong
        '853': '+853',  # Macau
        '855': '+855',  # Cambodia
        '856': '+856',  # Laos
        '880': '+880',  # Bangladesh
        '886': '+886',  # Taiwan
        '960': '+960',  # Maldives
        '961': '+961',  # Lebanon
        '962': '+962',  # Jordan
        '963': '+963',  # Syria
        '964': '+964',  # Iraq
        '965': '+965',  # Kuwait
        '966': '+966',  # Saudi Arabia
        '967': '+967',  # Yemen
        '968': '+968',  # Oman
        '970': '+970',  # Palestine
        '971': '+971',  # UAE
        '972': '+972',  # Israel
        '973': '+973',  # Bahrain
        '974': '+974',  # Qatar
        '975': '+975',  # Bhutan
        '976': '+976',  # Mongolia
        '977': '+977',  # Nepal
    }

    # Remove the + and check against known codes
    digits = phone_number[1:]

    # Try 3-digit codes first, then 2-digit, then 1-digit
    for length in [3, 2, 1]:
        if len(digits) >= length:
            code = digits[:length]
            if code in country_codes:
                return country_codes[code]

    # Fallback: return first 1-3 digits as prefix
    if len(digits) >= 1:
        return '+' + digits[:min(3, len(digits))]

    return None

@step_handler
def lambda_handler(event, context):
    """
    Lists all external type quick connects from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing quick connects from instance: {source_instance_id} in region: {source_region}")

        # Get quick connects from source
        response = connect_client.list_quick_connects(InstanceId=source_instance_id)
        quick_connects_summary = response['QuickConnectSummaryList']

        # Handle pagination if needed
        while 'NextToken' in response:
            response = connect_client.list_quick_connects(
                InstanceId=source_instance_id,
                NextToken=response['NextToken']
            )
            quick_connects_summary.extend(response['QuickConnectSummaryList'])

        quick_connects = []
        for qc_summary in quick_connects_summary:
            try:
                # Get detailed quick connect
                qc_detail = connect_client.describe_quick_connect(
                    InstanceId=source_instance_id,
                    QuickConnectId=qc_summary['Id']
                )

                # Filter for external type quick connects only
                qc = qc_detail['QuickConnect']
                quick_connects.append(qc)

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error describing quick connect {qc_summary['Id']}: {describe_error(e)}")
                continue

        # Convert to JSON serializable format
        quick_connects_json = json.loads(json.dumps(quick_connects, default=json_serial))

        # Extract phone number prefixes for PHONE_NUMBER type quick connects
        phone_prefixes = set()
        for qc in quick_connects_json:
            if qc.get('QuickConnectConfig', {}).get('QuickConnectType') == 'PHONE_NUMBER':
                phone_config = qc.get('QuickConnectConfig', {}).get('PhoneConfig', {})
                phone_number = phone_config.get('PhoneNumber', '')
                if phone_number:
                    # Extract country prefix (e.g., +1, +44, +91)
                    prefix = extract_phone_prefix(phone_number)
                    if prefix:
                        phone_prefixes.add(prefix)

        print(f"Successfully retrieved {len(quick_connects_json)} external quick connects")
        print(f"Phone number prefixes found: {redact(list(phone_prefixes))}")

        return {
            'statusCode': 200,
            'quickConnects': quick_connects_json,
            'phonePrefixes': list(phone_prefixes)
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing quick connects: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
