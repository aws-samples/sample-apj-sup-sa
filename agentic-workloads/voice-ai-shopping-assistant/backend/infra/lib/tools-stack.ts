import * as cdk from 'aws-cdk-lib/core';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as agentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as eventsources from 'aws-cdk-lib/aws-lambda-event-sources';
import { Duration, RemovalPolicy } from 'aws-cdk-lib/core';
import * as path from 'path';
import * as cp from 'child_process';
import { Construct } from 'constructs';

const TOOLS_ROOT = path.join(__dirname, '..', '..', 'tools');

/**
 * Tools & Gateway.
 *
 * Stands up a single AgentCore Gateway as the MCP entry point for the voice
 * agent. Tools (one Lambda per tool, each a `CfnGatewayTarget`) can be added
 * incrementally — the gateway is functional with zero targets, so the runtime
 * can integrate against a live MCP URL early.
 *
 * Inbound auth: AWS_IAM — the runtime's IAM role SigV4-signs its MCP calls,
 * no Cognito/OAuth infra. Tool search: SEMANTIC (create-time only; locked in).
 *
 * Exports (SSM, consumed by the AgentCore Runtime in AgentStack):
 *   /aisle/gateway/mcp_url  — the MCP endpoint  -> GATEWAY_MCP_URL env
 *   /aisle/gateway/arn      — gateway ARN       -> Resource in the IAM policy
 */
export class ToolsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Role the gateway assumes to invoke tool Lambdas (outbound). Empty for
    // now; each tool target will `grantInvoke` its Lambda onto this role.
    const gatewayRole = new iam.Role(this, 'GatewayExecRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'Execution role AgentCore Gateway assumes to invoke tool Lambdas',
    });

    const gateway = new agentcore.CfnGateway(this, 'AisleGateway', {
      name: 'aisle-gateway',
      roleArn: gatewayRole.roleArn,
      protocolType: 'MCP',
      authorizerType: 'AWS_IAM', // runtime SigV4-signs; no AuthorizerConfiguration needed
      description: 'Aisle voice assistant — unified MCP entry point for grocery tools',
      protocolConfiguration: {
        mcp: {
          searchType: 'SEMANTIC', // create-time only — cannot be added later
          instructions:
            'Grocery shopping tools for the Aisle voice assistant. Use these to ' +
            'search store inventory, compare product variants (brands, allergens, ' +
            'price, quality), look up recipes, suggest meals, and manage the ' +
            'shopper\'s cart and pickup order.',
        },
      },
    });

    // ---- Order observability (Phase 2) ----
    // S3 bucket for async-fulfillment artifacts (browser screenshots, DOM, logs).
    // The browser worker writes here; get_order_status presigns keys for the UI.
    const artifactsBucket = new s3.Bucket(this, 'OrderArtifactsBucket', {
      removalPolicy: RemovalPolicy.DESTROY, // demo: tear down with the stack
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      lifecycleRules: [{ expiration: Duration.days(7) }], // artifacts are demo-ephemeral
    });

    // SQS queue (+ DLQ) for async order fulfillment. create_order enqueues a
    // placed order; the place_order_async worker (below) consumes it and drives
    // the AgentCore browser through checkout. Visibility timeout > worker timeout.
    const fulfillmentDlq = new sqs.Queue(this, 'FulfillmentDlq', {
      queueName: 'aisle-fulfillment-dlq',
      retentionPeriod: Duration.days(14),
    });
    const fulfillmentQueue = new sqs.Queue(this, 'FulfillmentQueue', {
      queueName: 'aisle-fulfillment',
      visibilityTimeout: Duration.seconds(180),
      deadLetterQueue: { queue: fulfillmentDlq, maxReceiveCount: 2 },
    });

    // ---- Tool: search_products ----
    const searchProducts = this.dbToolLambda('SearchProductsFn', 'search_products');
    // Semantic search path embeds the query with Bedrock Cohere Embed v3.
    searchProducts.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-english-v3`,
      ],
    }));
    this.addLambdaTool(gateway, gatewayRole, searchProducts, {
      targetName: 'search-products',
      toolName: 'search_products',
      description:
        'Search the grocery store inventory and answer product-choice questions. ' +
        'Pass the shopper\'s words as `query` (multi-word is fine, e.g. "gluten free ' +
        'pasta", "dairy milk chocolate"). Use the OPTIONAL filters to answer questions ' +
        'about diet, allergies, price, quality and specials in ONE call instead of ' +
        'fetching then filtering: dietary_tags (must have ALL), exclude_allergens ' +
        '(must have NONE), min/max_price_cents (effective price, accounts for specials), ' +
        'quality_tier, in_stock_only, on_special_only, sort, and mode. Returns matching ' +
        'products with brand, price, size, allergens, dietary tags, and a `special` ' +
        'object when on special. Use whenever the shopper asks what\'s available, to ' +
        'find/compare an item, for the cheapest or on-special option, or for items that ' +
        'fit a diet or avoid an allergen. Handles both precise lookups and vague/' +
        'conceptual requests ("something fizzy", "taco night") via semantic search — ' +
        'just pass the shopper\'s words; mode=auto picks lexical or semantic for you.',
      inputSchema: {
        type: 'object',
        properties: {
          query: { type: 'string', description: 'Free-text search; words are matched independently against product name and brand. e.g. "milk", "gluten free pasta".' },
          category: { type: 'string', description: 'Optional category filter, e.g. "dairy", "bakery", "frozen food".' },
          limit: { type: 'integer', description: 'Max results (1-50, default 10).' },
          dietary_tags: {
            type: 'array',
            items: { type: 'string' },
            description: 'Only products carrying ALL of these dietary tags. Allowed values: vegetarian, vegan, gluten_free, low_sugar, low_salt, high_protein, halal, organic, kosher. e.g. ["vegan"], ["gluten_free","halal"].',
          },
          exclude_allergens: {
            type: 'array',
            items: { type: 'string' },
            description: 'Exclude products containing ANY of these allergens. Allowed values: milk, gluten, wheat, soy, fish, sulphites, egg, peanut, sesame, shellfish. Use when the shopper is allergic to or wants to avoid something, e.g. ["milk"], ["peanut","sesame"].',
          },
          min_price_cents: { type: 'integer', description: 'Minimum effective price in cents (special price if on special).' },
          max_price_cents: { type: 'integer', description: 'Maximum effective price in cents. e.g. 500 for "under $5".' },
          quality_tier: { type: 'string', description: 'Filter to a quality tier. Allowed values: value (budget/home brand), standard, premium (top-end).' },
          in_stock_only: { type: 'boolean', description: 'If true, only return products that are in stock.' },
          on_special_only: { type: 'boolean', description: 'If true, only return products currently on special.' },
          sort: { type: 'string', description: 'Result ordering. Allowed values: relevance (default, best match first), price_asc (cheapest first, for "cheapest"), price_desc (dearest first), savings_desc (biggest special saving first).' },
          mode: { type: 'string', description: 'Search mode. Allowed values: auto (default — keyword match, falling back to semantic when nothing matches), lexical (keyword/fuzzy only), semantic (meaning-based; best for vague/conceptual queries like "something fizzy" or "taco night"). Leave unset unless you specifically need to force one.' },
        },
        required: ['query'],
      },
    });

    // ---- Tool: add_to_cart ----
    const addToCart = this.dbToolLambda('AddToCartFn', 'add_to_cart');
    this.addLambdaTool(gateway, gatewayRole, addToCart, {
      targetName: 'add-to-cart',
      toolName: 'add_to_cart',
      description:
        'Add a product to the shopper\'s cart by product_id (from search_products), ' +
        'with a quantity. Creates the cart on first add; re-adding the same product ' +
        'increases its quantity. Returns the updated cart with line items and subtotal. ' +
        'Use when the shopper wants to buy, add, or put an item in their basket.',
      inputSchema: {
        type: 'object',
        properties: {
          session_id: { type: 'string', description: 'The shopper session id.' },
          product_id: { type: 'string', description: 'product_id of the item to add (from search_products).' },
          qty: { type: 'integer', description: 'Quantity to add (default 1).' },
        },
        required: ['session_id', 'product_id'],
      },
    });

    // ---- Tool: get_cart ----
    const getCart = this.dbToolLambda('GetCartFn', 'get_cart');
    this.addLambdaTool(gateway, gatewayRole, getCart, {
      targetName: 'get-cart',
      toolName: 'get_cart',
      description:
        'Get the shopper\'s current cart: all line items (name, quantity, price) and ' +
        'the subtotal. Use when the shopper asks what is in their cart/basket, or to ' +
        'review before ordering.',
      inputSchema: {
        type: 'object',
        properties: { session_id: { type: 'string', description: 'The shopper session id.' } },
        required: ['session_id'],
      },
    });

    // ---- Tool: remove_from_cart ----
    // Defined in CDK like the other tools so IaC stays authoritative.
    const removeFromCart = this.dbToolLambda('RemoveFromCartFn', 'remove_from_cart');
    this.addLambdaTool(gateway, gatewayRole, removeFromCart, {
      targetName: 'remove-from-cart',
      toolName: 'remove_from_cart',
      description:
        'Remove a product from the shopper\'s cart by product_id (from search/cart), ' +
        'or decrement its quantity if qty is given. Returns the updated cart. Use when ' +
        'the shopper wants to remove, delete, or take an item out of their cart or basket.',
      inputSchema: {
        type: 'object',
        properties: {
          session_id: { type: 'string', description: 'The shopper session id.' },
          product_id: { type: 'string', description: 'product_id of the item to remove.' },
          qty: { type: 'integer', description: 'Optional amount to remove; omit to remove the whole line.' },
        },
        required: ['session_id', 'product_id'],
      },
    });

    // ---- Merchant API (fake x402 store checkout) — internal infra, not a tool ----
    // Only create_order calls it (server-to-server), so it sits behind an
    // IAM-authorized API Gateway REST API — NOT a public Function URL. A
    // Function URL only supports AuthType NONE | AWS_IAM, and NONE forces a
    // Principal:"*" resource policy (open to the internet), which was flagged as
    // an open-Lambda-policy security finding. The REST API gives a first-class
    // auth boundary; unsigned requests get 403. Funded testnet wallet address is
    // injected so the x402 requirement names a real payTo.
    const merchant = new lambda.Function(this, 'MerchantApiFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(TOOLS_ROOT, 'merchant_api')),
      timeout: Duration.seconds(15),
      memorySize: 128,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: {
        PAYTO_ADDRESS: process.env.AISLE_PAYTO_ADDRESS ?? '0x0000000000000000000000000000000000000000',
        NETWORK: 'base-sepolia',
        AMOUNT_ATOMIC: '10000', // 0.01 USDC (6dp) — token charge, not the grocery total
      },
    });

    // Regional REST API → POST / with AWS_IAM authorization → Lambda proxy. The
    // route is not public; create_order is the only principal granted
    // execute-api:Invoke (below) and SigV4-signs its requests.
    const merchantApi = new apigw.RestApi(this, 'MerchantApi', {
      restApiName: 'aisle-merchant-api',
      description: 'IAM-authorized front door for the x402 merchant (replaces public Function URL)',
      endpointConfiguration: { types: [apigw.EndpointType.REGIONAL] },
      deployOptions: { stageName: 'prod' },
    });
    merchantApi.root.addMethod('POST', new apigw.LambdaIntegration(merchant, { proxy: true }), {
      authorizationType: apigw.AuthorizationType.IAM,
    });
    const merchantUrl = merchantApi.url; // API stage URL; create_order POSTs here (SigV4-signed)

    // ---- Tool: create_order — payment-gated ----
    // Payment leg is flag-gated: PAYMENTS_ENABLED + the payment resource ids come
    // from the AgentCore Payments setup script (env at deploy time). Off by default
    // so the order flow is demoable before the testnet wallet is funded.
    const createOrder = this.dbToolLambda('CreateOrderFn', 'create_order', {
      timeout: Duration.seconds(60),
      bundleDeps: true, // needs current boto3 for ProcessPayment (preview API)
      environment: {
        MERCHANT_URL: merchantUrl,
        PAYMENTS_ENABLED: process.env.AISLE_PAYMENTS_ENABLED ?? 'false',
        PAYMENT_MANAGER_ARN: process.env.AISLE_PAYMENT_MANAGER_ARN ?? '',
        PAYMENT_INSTRUMENT_ID: process.env.AISLE_PAYMENT_INSTRUMENT_ID ?? '',
        PAYMENT_USER_ID: process.env.AISLE_PAYMENT_USER_ID ?? '',
        // Payment sessions have a 60-min TTL, so create_order mints one at
        // runtime (no baked-in session id). This caps per-session spend.
        PAYMENT_MAX_SPEND_USD: process.env.AISLE_PAYMENT_MAX_SPEND_USD ?? '100.00',
        // Hand a placed order off to async browser fulfillment.
        FULFILLMENT_QUEUE_URL: fulfillmentQueue.queueUrl,
        // TEST_MODE: always report the order as placed to the agent (the async
        // worker still runs + logs real steps). Off => report true interim status.
        TEST_MODE: process.env.AISLE_TEST_MODE ?? 'true',
      },
    });
    // create_order enqueues the placed order for the async browser worker.
    fulfillmentQueue.grantSendMessages(createOrder);
    // Allow the real x402 payment call when enabled: ProcessPayment plus the
    // runtime session lifecycle (create_order mints its own payment session).
    createOrder.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'bedrock-agentcore:ProcessPayment',
        'bedrock-agentcore:CreatePaymentSession',
        'bedrock-agentcore:GetPaymentSession',
      ],
      resources: ['*'],
    }));
    // create_order is the only principal allowed to invoke the merchant API; it
    // SigV4-signs the POST as the execute-api service.
    createOrder.addToRolePolicy(new iam.PolicyStatement({
      actions: ['execute-api:Invoke'],
      resources: [merchantApi.arnForExecuteApi('POST', '/', 'prod')],
    }));
    this.addLambdaTool(gateway, gatewayRole, createOrder, {
      targetName: 'create-order',
      toolName: 'create_order',
      description:
        'Place the shopper\'s cart as an order and complete checkout. Routes by merchant: ' +
        'an x402-native merchant is paid directly via AgentCore Payments; a card-only ' +
        'merchant is fulfilled asynchronously by the AgentCore Browser paying with an ' +
        'issued card. Returns the order with its code and status. Use when the shopper ' +
        'is ready to order, buy, or check out their cart.',
      inputSchema: {
        type: 'object',
        properties: {
          session_id: { type: 'string', description: 'The shopper session id.' },
          merchant_id: { type: 'string', description: 'Which merchant to order from (default "aisle-grocery"). e.g. "aisle-grocery" (card-only, browser checkout) or "delivery-slot" (x402-native, paid directly).' },
          pickup_time: { type: 'string', description: 'Optional preferred pickup time (ISO-8601).' },
        },
        required: ['session_id'],
      },
    });

    // ---- Tool: get_grocery_list (UC1) ----
    const getGroceryList = this.dbToolLambda('GetGroceryListFn', 'get_grocery_list');
    this.addLambdaTool(gateway, gatewayRole, getGroceryList, {
      targetName: 'get-grocery-list',
      toolName: 'get_grocery_list',
      description:
        'Get the shopper\'s persistent grocery list (items they still need), by user_id. ' +
        'The list survives across sessions (unlike the cart). Use when the shopper asks ' +
        'what is on their list, or before ordering. Returns each item with its raw text, ' +
        'resolved product (if matched), quantity and status.',
      inputSchema: {
        type: 'object',
        properties: { user_id: { type: 'string', description: 'The shopper\'s persistent user id.' } },
        required: ['user_id'],
      },
    });

    // ---- Tool: update_grocery_list (UC1) ----
    const updateGroceryList = this.dbToolLambda('UpdateGroceryListFn', 'update_grocery_list');
    this.addLambdaTool(gateway, gatewayRole, updateGroceryList, {
      targetName: 'update-grocery-list',
      toolName: 'update_grocery_list',
      description:
        'Update the shopper\'s persistent grocery list and return the updated list. ' +
        'add: new items by raw text ("bread", with optional qty). To link an item to a ' +
        'real catalogue product (so it can be ordered and matched against specials), ' +
        'first find it with search_products and pass that product_id + name on add (or ' +
        'patch an existing item via update). remove: drop items by item_id. update: ' +
        'change an item\'s qty, status (active=still needed, have=already got it, ' +
        'out_of_stock, removed), and/or set product_id + name once resolved. Use when ' +
        'the shopper adds to, ticks off, or edits their list.',
      inputSchema: {
        type: 'object',
        properties: {
          user_id: { type: 'string', description: 'The shopper\'s persistent user id.' },
          add: { type: 'array', items: { type: 'object', properties: { raw_text: { type: 'string', description: 'What to add, e.g. "bread".' }, qty: { type: 'number', description: 'Quantity (default 1).' }, product_id: { type: 'string', description: 'Optional resolved catalogue product_id (from search_products).' }, name: { type: 'string', description: 'Optional resolved product name snapshot.' } }, required: ['raw_text'] }, description: 'Items to add.' },
          remove: { type: 'array', items: { type: 'string' }, description: 'item_ids to remove from the list.' },
          update: { type: 'array', items: { type: 'object', properties: { item_id: { type: 'string' }, qty: { type: 'number' }, status: { type: 'string', description: 'active | have | out_of_stock | removed' }, product_id: { type: 'string', description: 'Set/replace the resolved catalogue product_id (from search_products).' }, name: { type: 'string', description: 'Set/replace the resolved product name.' } }, required: ['item_id'] }, description: 'Items to change (qty, status, and/or resolved product_id + name).' },
        },
        required: ['user_id'],
      },
    });

    // ---- Tool: get_offers (UC5) ----
    const getOffers = this.dbToolLambda('GetOffersFn', 'get_offers');
    // Search mode embeds query terms (semantic fallback) via Bedrock Cohere v3.
    getOffers.addToRolePolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel'],
      resources: [`arn:aws:bedrock:${this.region}::foundation-model/cohere.embed-english-v3`],
    }));
    this.addLambdaTool(gateway, gatewayRole, getOffers, {
      targetName: 'get-offers',
      toolName: 'get_offers',
      description:
        'Find grocery items that are CURRENTLY ON SPECIAL. Two modes. (1) Browse: call ' +
        'with no queries (optionally a category) to get the top current specials, ' +
        'biggest savings first. (2) Search: pass `queries` — a list of terms — to find ' +
        'specials matching each. Multi-word is fine ("gluten free pasta"), and you can ' +
        'pass several terms at once: e.g. the ingredients of a recipe ' +
        '(["pasta","bacon","eggs","parmesan"]) to see which have a deal. Results come ' +
        'back grouped per query (with an empty list where nothing\'s on special), plus a ' +
        'flattened savings-sorted list. Each offer includes pct_below_usual. Use for ' +
        '"what\'s on special", "any deals on X", or "specials on the ingredients for ' +
        '<dish>". Reflects current specials only — not historical/seasonal pricing.',
      inputSchema: {
        type: 'object',
        properties: {
          queries: { type: 'array', items: { type: 'string' }, description: 'Terms to find specials for, e.g. ["pasta"] or a recipe\'s ingredients ["pasta","bacon","eggs"]. Multi-word terms allowed. Omit to browse all current specials.' },
          category: { type: 'string', description: 'Optional category narrowing, e.g. "dairy", "drinks".' },
          dietary_tags: { type: 'array', items: { type: 'string' }, description: 'Only specials whose product has ALL these tags. Allowed: vegetarian, vegan, gluten_free, low_sugar, low_salt, high_protein, halal, organic, kosher.' },
          exclude_allergens: { type: 'array', items: { type: 'string' }, description: 'Exclude specials whose product contains ANY of these allergens. Allowed: milk, gluten, wheat, soy, fish, sulphites, egg, peanut, sesame, shellfish.' },
          per_query_limit: { type: 'integer', description: 'Max offers returned per query term (1-20, default 5).' },
          limit: { type: 'integer', description: 'Browse mode only (no queries): max offers (1-50, default 10).' },
          sort: { type: 'string', description: 'Result ordering within each query (search mode). Allowed: relevance (default), savings_desc (biggest saving first), price_asc, price_desc.' },
        },
      },
    });

    // ---- Tool: check_relevant_changes (UC5) ----
    const checkChanges = this.dbToolLambda('CheckRelevantChangesFn', 'check_relevant_changes');
    this.addLambdaTool(gateway, gatewayRole, checkChanges, {
      targetName: 'check-relevant-changes',
      toolName: 'check_relevant_changes',
      description:
        'Check what changed that is relevant to THIS shopper: items on their grocery ' +
        'list that are currently on special (with savings) or now out of stock. Use on ' +
        'connect, or when the shopper asks if anything on their list is on special / ' +
        'cheaper. Returns a list of changes, biggest saving first.',
      inputSchema: {
        type: 'object',
        properties: { user_id: { type: 'string', description: 'The shopper\'s persistent user id.' } },
        required: ['user_id'],
      },
    });

    // ---- Tool: get_order_status (Phase 2 — order observability) ----
    const getOrderStatus = this.dbToolLambda('GetOrderStatusFn', 'get_order_status', {
      environment: { ARTIFACTS_BUCKET: artifactsBucket.bucketName },
    });
    artifactsBucket.grantRead(getOrderStatus); // presign screenshot/dom/log keys
    this.addLambdaTool(gateway, gatewayRole, getOrderStatus, {
      targetName: 'get-order-status',
      toolName: 'get_order_status',
      description:
        'Get the full status and history of an order: its current state (paid, ' +
        'placing, placed, declined_insufficient_funds, ...), a timeline of what ' +
        'happened (payment processed, balance checked, items added to the store, ' +
        'reached checkout, declined/placed), the payment audit trail, and any browser ' +
        'screenshots captured while placing it. Pass order_id, or session_id for the ' +
        'shopper\'s latest order. Use when the shopper asks "did my order go through?", ' +
        '"what\'s happening with my order?", or to report progress after checkout.',
      inputSchema: {
        type: 'object',
        properties: {
          order_id: { type: 'string', description: 'The order to look up (from create_order).' },
          session_id: { type: 'string', description: 'Alternatively, the shopper session id — returns their most recent order.' },
        },
      },
    });

    // Stripe Issuing secret (test mode). STRIPE_MODE gates real vs simulated
    // issuing/authorization — see docs/STRIPE_ISSUING.md. Default 'simulation'
    // (Stripe Issuing is geo/approval-gated); a fork sets AISLE_STRIPE_MODE=live.
    const STRIPE_SECRET_NAME = '/aisle/stripe/secret_key';
    const STRIPE_MODE = process.env.AISLE_STRIPE_MODE ?? 'simulation';
    const stripeSecretArn = cdk.Arn.format(
      { service: 'secretsmanager', resource: 'secret', resourceName: 'aisle/stripe/secret_key*',
        arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME }, this);
    const grantStripeRead = (fn: lambda.Function) => fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'], resources: [stripeSecretArn],
    }));

    // ---- x402 -> card broker (Phase 2) ----
    // The crypto->card conversion: an x402 merchant whose product is a funded
    // virtual card. The worker pays its 402 invoice for real with AgentCore
    // Payments; the broker then issues the card via Stripe Issuing (STRIPE_MODE=
    // live) or a Stripe-shaped simulation (default). Behind an IAM REST API.
    const cardBroker = this.dbToolLambda('CardBrokerFn', 'card_broker', {
      timeout: Duration.seconds(30),
      bundleDeps: true, // bundles the stripe SDK (pure Python, no Docker)
      environment: {
        PAYTO_ADDRESS: process.env.AISLE_PAYTO_ADDRESS ?? '0x0000000000000000000000000000000000000000',
        ASSET_ADDRESS: process.env.AISLE_ASSET_ADDRESS ?? '0x036CbD53842c5426634e7929541eC2318f3dCF7e',
        NETWORK: 'base-sepolia',
        STRIPE_SECRET_NAME, STRIPE_MODE, STRIPE_CURRENCY: 'usd',
      },
    });
    grantStripeRead(cardBroker);
    const cardBrokerApi = new apigw.RestApi(this, 'CardBrokerApi', {
      restApiName: 'aisle-card-broker-api',
      description: 'IAM-authorized x402 broker that mints funded virtual cards (worker SigV4-signs)',
      endpointConfiguration: { types: [apigw.EndpointType.REGIONAL] },
      deployOptions: { stageName: 'prod' },
    });
    cardBrokerApi.root.addMethod('ANY', new apigw.LambdaIntegration(cardBroker, { proxy: true }), {
      authorizationType: apigw.AuthorizationType.IAM,
    });
    const cardBrokerUrl = cardBrokerApi.url; // worker POSTs here (SigV4-signed)

    // ---- x402-native example merchant: priority delivery slot ----
    // Demonstrates create_order's x402 pathway — a paid service the agent pays
    // for DIRECTLY via AgentCore Payments (no card, no browser). Behind an IAM
    // REST API; create_order SigV4-signs. Its endpoint is the 'delivery-slot'
    // merchants row.
    const deliveryApi = this.dbToolLambda('DeliveryApiFn', 'delivery_api', {
      timeout: Duration.seconds(15),
      environment: {
        PAYTO_ADDRESS: process.env.AISLE_PAYTO_ADDRESS ?? '0x0000000000000000000000000000000000000000',
        ASSET_ADDRESS: process.env.AISLE_ASSET_ADDRESS ?? '0x036CbD53842c5426634e7929541eC2318f3dCF7e',
        NETWORK: 'base-sepolia', SLOT_FEE_CENTS: '199',
      },
    });
    const deliveryApiGw = new apigw.RestApi(this, 'DeliveryApi', {
      restApiName: 'aisle-delivery-api',
      description: 'IAM-authorized x402-native merchant (priority delivery slot)',
      endpointConfiguration: { types: [apigw.EndpointType.REGIONAL] },
      deployOptions: { stageName: 'prod' },
    });
    deliveryApiGw.root.addMethod('ANY', new apigw.LambdaIntegration(deliveryApi, { proxy: true }), {
      authorizationType: apigw.AuthorizationType.IAM,
    });
    const deliveryApiUrl = deliveryApiGw.url;
    // create_order pays this merchant's x402 endpoint directly.
    createOrder.addToRolePolicy(new iam.PolicyStatement({
      actions: ['execute-api:Invoke'],
      resources: [deliveryApiGw.arnForExecuteApi()],
    }));

    // ---- Async fulfillment: storefront + AgentCore browser worker (Phase 2) ----
    // The storefront is a minimal grocery checkout (seeded from the order's
    // Aurora items) that the AgentCore browser drives. It is NOT public — it
    // sits behind an IAM-authorized REST API and the worker SigV4-signs each
    // navigation. GET /pay validates a broker-issued virtual card so a browser
    // navigation can submit payment without a POST body.
    const storefront = this.dbToolLambda('StorefrontFn', 'storefront', {
      timeout: Duration.seconds(30),
    });
    const storefrontApi = new apigw.RestApi(this, 'StorefrontApi', {
      restApiName: 'aisle-storefront-api',
      description: 'IAM-authorized x402 grocery storefront the AgentCore browser drives',
      endpointConfiguration: { types: [apigw.EndpointType.REGIONAL] },
      deployOptions: { stageName: 'prod' },
    });
    const storefrontIntegration = new apigw.LambdaIntegration(storefront, { proxy: true });
    // Root (/) renders the checkout page; {proxy+} covers /pay and any sub-path.
    storefrontApi.root.addMethod('ANY', storefrontIntegration, {
      authorizationType: apigw.AuthorizationType.IAM,
    });
    storefrontApi.root.addProxy({
      anyMethod: true,
      defaultIntegration: storefrontIntegration,
      defaultMethodOptions: { authorizationType: apigw.AuthorizationType.IAM },
    });
    // The REST API id the worker needs to build + SigV4-sign storefront URLs.
    const storefrontApiId = storefrontApi.restApiId;

    // place_order_async — SQS-triggered worker. Runs the real USDC balance gate,
    // BUYS a funded virtual card from the x402 broker (real AgentCore payment),
    // then drives the AgentCore managed browser over raw CDP (bundles boto3 +
    // websocket-client; no Docker) through the storefront checkout with that
    // card. Writes lifecycle events + screenshots for observability.
    const worker = this.dbToolLambda('PlaceOrderAsyncFn', 'place_order_async', {
      timeout: Duration.seconds(120),
      memorySize: 512,
      bundleDeps: true,
      environment: {
        STOREFRONT_API_ID: storefrontApiId,
        CARD_BROKER_URL: cardBrokerUrl,
        ARTIFACTS_BUCKET: artifactsBucket.bucketName,
        PAYMENT_MANAGER_ARN: process.env.AISLE_PAYMENT_MANAGER_ARN ?? '',
        PAYMENT_CONNECTOR_ID: process.env.AISLE_PAYMENT_CONNECTOR_ID ?? '',
        PAYMENT_INSTRUMENT_ID: process.env.AISLE_PAYMENT_INSTRUMENT_ID ?? '',
        PAYMENT_USER_ID: process.env.AISLE_PAYMENT_USER_ID ?? 'aisle-demo-user',
        PAYMENT_MAX_SPEND_USD: process.env.AISLE_PAYMENT_MAX_SPEND_USD ?? '100.00',
        BALANCE_CHAIN: process.env.AISLE_BALANCE_CHAIN ?? 'BASE_SEPOLIA',
        BALANCE_TOKEN: process.env.AISLE_BALANCE_TOKEN ?? 'USDC',
        // Stripe card authorization (live test-helper auth vs local simulation).
        STRIPE_SECRET_NAME, STRIPE_MODE,
      },
    });
    grantStripeRead(worker);                     // Stripe auth (live mode)
    artifactsBucket.grantWrite(worker);          // screenshots -> S3
    fulfillmentQueue.grantConsumeMessages(worker);
    worker.addEventSource(new eventsources.SqsEventSource(fulfillmentQueue, { batchSize: 1 }));
    // x402 balance gate + payment (incl. the runtime payment session the worker
    // mints), and the managed browser session lifecycle.
    worker.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'bedrock-agentcore:GetPaymentInstrumentBalance',
        'bedrock-agentcore:ProcessPayment',
        'bedrock-agentcore:CreatePaymentSession',
        'bedrock-agentcore:GetPaymentSession',
        'bedrock-agentcore:StartBrowserSession',
        'bedrock-agentcore:StopBrowserSession',
        'bedrock-agentcore:GetBrowserSession',
        'bedrock-agentcore:UpdateBrowserStream',
        'bedrock-agentcore:ConnectBrowserAutomationStream',
        'bedrock-agentcore:ConnectBrowserLiveViewStream',
      ],
      resources: ['*'],
    }));
    // The worker SigV4-signs storefront navigations AND card-broker calls (execute-api).
    worker.addToRolePolicy(new iam.PolicyStatement({
      actions: ['execute-api:Invoke'],
      resources: [storefrontApi.arnForExecuteApi(), cardBrokerApi.arnForExecuteApi()],
    }));

    // ---- SSM exports (cross-stack handoff, never hard refs) ----
    new ssm.StringParameter(this, 'StorefrontApiIdParam', {
      parameterName: '/aisle/storefront/api_id',
      stringValue: storefrontApiId,
      description: 'REST API id of the IAM-authorized x402 storefront (worker SigV4-signs it).',
    });
    new ssm.StringParameter(this, 'FulfillmentQueueUrlParam', {
      parameterName: '/aisle/orders/fulfillment_queue_url',
      stringValue: fulfillmentQueue.queueUrl,
      description: 'SQS queue create_order enqueues placed orders to for async browser fulfillment.',
    });
    new ssm.StringParameter(this, 'CardBrokerUrlParam', {
      parameterName: '/aisle/orders/card_broker_url',
      stringValue: cardBrokerUrl,
      description: 'IAM-authorized x402 card broker URL the worker pays to mint a funded virtual card.',
    });
    // Endpoints for the `merchants` rows (the create_order router's source of
    // truth). The merchants table is seeded with these: aisle-grocery ->
    // storefront (browser path), delivery-slot -> delivery API (x402 path).
    new ssm.StringParameter(this, 'StorefrontUrlParam', {
      parameterName: '/aisle/merchants/aisle-grocery/endpoint',
      stringValue: storefrontApi.url,
      description: "merchants.endpoint for 'aisle-grocery' (browser pathway).",
    });
    new ssm.StringParameter(this, 'DeliveryApiUrlParam', {
      parameterName: '/aisle/merchants/delivery-slot/endpoint',
      stringValue: deliveryApiUrl,
      description: "merchants.endpoint for 'delivery-slot' (x402 pathway).",
    });
    new ssm.StringParameter(this, 'ArtifactsBucketParam', {
      parameterName: '/aisle/orders/artifacts_bucket',
      stringValue: artifactsBucket.bucketName,
      description: 'S3 bucket for order fulfillment artifacts (browser screenshots, etc.)',
    });
    new ssm.StringParameter(this, 'MerchantUrlParam', {
      parameterName: '/aisle/merchant/url',
      stringValue: merchantUrl,
      description: 'Fake x402 merchant checkout endpoint (IAM-authorized REST API, internal).',
    });
    new ssm.StringParameter(this, 'GatewayUrlParam', {
      parameterName: '/aisle/gateway/mcp_url',
      stringValue: gateway.attrGatewayUrl,
      description: 'AgentCore Gateway MCP endpoint — runtime GATEWAY_MCP_URL',
    });

    new ssm.StringParameter(this, 'GatewayArnParam', {
      parameterName: '/aisle/gateway/arn',
      stringValue: gateway.attrGatewayArn,
      description: 'AgentCore Gateway ARN — Resource in runtime role IAM policy',
    });

    // Surfaced on `cdk deploy` so the URL is visible without an SSM lookup.
    new cdk.CfnOutput(this, 'GatewayUrl', { value: gateway.attrGatewayUrl });
    new cdk.CfnOutput(this, 'GatewayArn', { value: gateway.attrGatewayArn });
    new cdk.CfnOutput(this, 'GatewayId', { value: gateway.attrGatewayIdentifier });
  }

  /**
   * Create a Python tool Lambda bundled from backend/tools/<dir>/ and grant it
   * Aurora Data API access (read DB coords from SSM /aisle/db/*, ExecuteStatement,
   * and the cluster's master secret). No hard cross-stack ref, so ToolsStack
   * deploys independently of DataStack once the SSM params exist.
   */
  private dbToolLambda(
    id: string,
    dir: string,
    opts: { timeout?: Duration; memorySize?: number; environment?: Record<string, string>; bundleDeps?: boolean } = {},
  ): lambda.Function {
    // If the tool has a requirements.txt (e.g. create_order needs a current
    // boto3 for the preview ProcessPayment API — Lambda's built-in lags), pip
    // install it alongside the handler. boto3 is pure-Python, so we can bundle
    // locally without Docker.
    const code = opts.bundleDeps
      ? lambda.Code.fromAsset(path.join(TOOLS_ROOT, dir), {
          bundling: {
            image: lambda.Runtime.PYTHON_3_12.bundlingImage,
            local: {
              tryBundle(outputDir: string): boolean {
                const src = path.join(TOOLS_ROOT, dir);
                cp.execSync(
                  `python3 -m pip install -r "${path.join(src, 'requirements.txt')}" -t "${outputDir}" -q && ` +
                  `cp "${path.join(src, 'handler.py')}" "${outputDir}"`,
                  { stdio: 'inherit' },
                );
                return true;
              },
            },
            command: [
              'bash', '-c',
              'pip install -r requirements.txt -t /asset-output && cp handler.py /asset-output',
            ],
          },
        })
      : lambda.Code.fromAsset(path.join(TOOLS_ROOT, dir));

    const fn = new lambda.Function(this, id, {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: 'handler.handler',
      code,
      timeout: opts.timeout ?? Duration.seconds(30),
      memorySize: opts.memorySize ?? 256,
      logRetention: logs.RetentionDays.ONE_WEEK,
      environment: opts.environment,
    });
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameters'],
      resources: [cdk.Arn.format({ service: 'ssm', resource: 'parameter', resourceName: 'aisle/db/*' }, this)],
    }));
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['rds-data:ExecuteStatement', 'rds-data:BatchExecuteStatement'],
      resources: ['*'], // Data API resource ARNs aren't known cross-stack
    }));
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['secretsmanager:GetSecretValue'],
      resources: [cdk.Arn.format({ service: 'secretsmanager', resource: 'secret', resourceName: 'ClusterSecret*', arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME }, this)],
    }));
    return fn;
  }

  /**
   * Register a Lambda as an MCP tool target on the gateway. Grants the gateway
   * role permission to invoke the function and declares the tool's inline
   * schema. Tool name == target name keeps the aggregated `<target>___<tool>`
   * address readable. The description is the SEMANTIC-search key — phrase it in
   * shopper-intent language.
   */
  private addLambdaTool(
    gateway: agentcore.CfnGateway,
    gatewayRole: iam.IRole,
    fn: lambda.Function,
    tool: {
      targetName: string;  // kebab-case, ^([0-9a-zA-Z][-]?){1,100}$
      toolName: string;    // MCP tool id the agent calls (snake_case)
      description: string;
      inputSchema: agentcore.CfnGatewayTarget.SchemaDefinitionProperty;
    },
  ): agentcore.CfnGatewayTarget {
    fn.grantInvoke(gatewayRole);
    // Target Description is capped at 200 chars; the rich SEMANTIC-search text
    // lives on the tool definition (uncapped) instead.
    return new agentcore.CfnGatewayTarget(this, `${tool.targetName}-target`, {
      gatewayIdentifier: gateway.attrGatewayIdentifier,
      name: tool.targetName,
      description: `Tool target for ${tool.toolName}.`,
      credentialProviderConfigurations: [{ credentialProviderType: 'GATEWAY_IAM_ROLE' }],
      targetConfiguration: {
        mcp: {
          lambda: {
            lambdaArn: fn.functionArn,
            toolSchema: {
              inlinePayload: [{
                name: tool.toolName,
                description: tool.description,
                inputSchema: tool.inputSchema,
              }],
            },
          },
        },
      },
    });
  }
}
