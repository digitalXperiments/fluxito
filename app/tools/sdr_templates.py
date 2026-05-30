"""
Industry template skeletons for SDR bootstrap.

Each template defines the standard events for a business vertical.
generate_sdr overlays these onto discovered events — template events
that don't match discovered implementation get [TODO] markers.

5 templates: ecommerce, saas, lead_gen, media, app.
"""

from __future__ import annotations

from app.tools.sdr_parser import ParsedDestination, ParsedEvent, ParsedParameter


def get_industry_template(business_type: str) -> list[ParsedEvent]:
    """Return template events for a business type, or empty list if unknown."""
    templates = {
        "ecommerce": _ecommerce_template,
        "saas": _saas_template,
        "lead_gen": _lead_gen_template,
        "media": _media_template,
        "app": _app_template,
        "marketplace": _ecommerce_template,  # marketplace reuses ecommerce base
    }
    factory = templates.get(business_type)
    return factory() if factory else []


# ---------------------------------------------------------------------------
# Ecommerce
# ---------------------------------------------------------------------------


def _ecommerce_template() -> list[ParsedEvent]:
    return [
        ParsedEvent(
            name="view_item_list",
            purpose="User viewed a product listing page (category, search results, recommendations). Measures browsing engagement and category performance.",
            trigger_type="pageview",
            trigger_config={"configuration": "Category/search/collection pages"},
            status="planned",
            parameters=[
                ParsedParameter(name="item_list_id", type="string", required=False, example="category_shoes"),
                ParsedParameter(name="item_list_name", type="string", required=False, example="Shoes"),
                ParsedParameter(
                    name="items",
                    type="array",
                    required=True,
                    source="dataLayer.ecommerce.items",
                    example="[{item_id, item_name, price}]",
                ),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="select_item",
            purpose="User clicked a product from a listing/search/recommendation. Connects listing performance to product detail views.",
            trigger_type="click",
            trigger_config={"configuration": "Product tile/link click within a listing"},
            status="planned",
            parameters=[
                ParsedParameter(name="item_list_id", type="string", required=False, example="category_shoes"),
                ParsedParameter(name="item_list_name", type="string", required=False, example="Shoes"),
                ParsedParameter(
                    name="items",
                    type="array",
                    required=True,
                    source="dataLayer.ecommerce.items",
                    example="[{item_id, item_name, index}]",
                ),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="view_item",
            purpose="User viewed a product detail page. Key upper-funnel event for measuring product interest and calculating view-to-cart rate.",
            trigger_type="pageview",
            trigger_config={"configuration": "Product detail pages"},
            status="planned",
            parameters=[
                ParsedParameter(
                    name="currency", type="string", required=True, example="USD", validation_rule="ISO 4217"
                ),
                ParsedParameter(
                    name="value",
                    type="number",
                    required=True,
                    source="dataLayer.ecommerce.value",
                    example="49.99",
                    validation_rule="must be >= 0",
                ),
                ParsedParameter(
                    name="items",
                    type="array",
                    required=True,
                    source="dataLayer.ecommerce.items",
                    example="[{item_id, item_name, price, quantity}]",
                ),
            ],
            destinations=[
                ParsedDestination(platform="ga4"),
                ParsedDestination(platform="meta", dest_event_name="ViewContent"),
            ],
        ),
        ParsedEvent(
            name="add_to_cart",
            purpose="User added a product to their shopping cart. Critical mid-funnel conversion event. Measures purchase intent.",
            trigger_type="click",
            trigger_config={"configuration": "Add-to-cart button click or dataLayer event"},
            status="planned",
            parameters=[
                ParsedParameter(
                    name="currency", type="string", required=True, example="USD", validation_rule="ISO 4217"
                ),
                ParsedParameter(
                    name="value", type="number", required=True, example="49.99", validation_rule="must be > 0"
                ),
                ParsedParameter(
                    name="items",
                    type="array",
                    required=True,
                    source="dataLayer.ecommerce.items",
                    example="[{item_id, item_name, price, quantity}]",
                ),
            ],
            destinations=[
                ParsedDestination(platform="ga4"),
                ParsedDestination(platform="meta", dest_event_name="AddToCart"),
            ],
        ),
        ParsedEvent(
            name="view_cart",
            purpose="User viewed their shopping cart. Measures checkout-intent funnel between add-to-cart and checkout.",
            trigger_type="pageview",
            trigger_config={"configuration": "Cart page load or cart drawer open"},
            status="planned",
            parameters=[
                ParsedParameter(name="currency", type="string", required=True, example="USD"),
                ParsedParameter(name="value", type="number", required=True, example="49.99"),
                ParsedParameter(
                    name="items", type="array", required=True, source="dataLayer.ecommerce.items"
                ),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="remove_from_cart",
            purpose="User removed a product from the cart. Signals friction/price sensitivity in the mid-funnel.",
            trigger_type="click",
            trigger_config={"configuration": "Remove/delete control in cart"},
            status="planned",
            parameters=[
                ParsedParameter(name="currency", type="string", required=True, example="USD"),
                ParsedParameter(name="value", type="number", required=True, example="49.99"),
                ParsedParameter(
                    name="items", type="array", required=True, source="dataLayer.ecommerce.items"
                ),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="begin_checkout",
            purpose="User initiated the checkout process. Measures checkout funnel entry.",
            trigger_type="datalayer_event",
            trigger_config={"configuration": "begin_checkout dataLayer event or checkout page load"},
            status="planned",
            parameters=[
                ParsedParameter(name="currency", type="string", required=True, example="USD"),
                ParsedParameter(name="value", type="number", required=True, example="99.99"),
                ParsedParameter(
                    name="items", type="array", required=True, source="dataLayer.ecommerce.items"
                ),
                ParsedParameter(name="coupon", type="string", required=False, example="SAVE10"),
            ],
            destinations=[
                ParsedDestination(platform="ga4"),
                ParsedDestination(platform="meta", dest_event_name="InitiateCheckout"),
            ],
        ),
        ParsedEvent(
            name="add_shipping_info",
            purpose="User submitted shipping details during checkout. Measures progression through the checkout funnel.",
            trigger_type="datalayer_event",
            trigger_config={"configuration": "add_shipping_info dataLayer event"},
            status="planned",
            parameters=[
                ParsedParameter(name="currency", type="string", required=True, example="USD"),
                ParsedParameter(name="value", type="number", required=True, example="99.99"),
                ParsedParameter(name="shipping_tier", type="string", required=False, example="Ground"),
                ParsedParameter(
                    name="items", type="array", required=True, source="dataLayer.ecommerce.items"
                ),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="add_payment_info",
            purpose="User submitted payment information during checkout. Measures checkout completion intent.",
            trigger_type="datalayer_event",
            trigger_config={"configuration": "add_payment_info dataLayer event"},
            status="planned",
            parameters=[
                ParsedParameter(name="currency", type="string", required=True, example="USD"),
                ParsedParameter(name="value", type="number", required=True, example="99.99"),
                ParsedParameter(name="payment_type", type="string", required=False, example="credit_card"),
            ],
            destinations=[
                ParsedDestination(platform="ga4"),
                ParsedDestination(platform="meta", dest_event_name="AddPaymentInfo"),
            ],
        ),
        ParsedEvent(
            name="purchase",
            purpose="Transaction completed. The primary conversion event. Measures revenue, AOV, and ROI across all marketing channels.",
            trigger_type="datalayer_event",
            trigger_config={"configuration": "purchase dataLayer event on order confirmation page"},
            status="planned",
            parameters=[
                ParsedParameter(
                    name="transaction_id",
                    type="string",
                    required=True,
                    example="T-12345",
                    validation_rule="must be unique per transaction",
                ),
                ParsedParameter(
                    name="currency", type="string", required=True, example="USD", validation_rule="ISO 4217"
                ),
                ParsedParameter(
                    name="value", type="number", required=True, example="99.99", validation_rule="must be > 0"
                ),
                ParsedParameter(name="tax", type="number", required=False, example="8.99"),
                ParsedParameter(name="shipping", type="number", required=False, example="5.99"),
                ParsedParameter(name="coupon", type="string", required=False, example="SAVE10"),
                ParsedParameter(
                    name="items",
                    type="array",
                    required=True,
                    source="dataLayer.ecommerce.items",
                    example="[{item_id, item_name, price, quantity}]",
                ),
            ],
            destinations=[
                ParsedDestination(platform="ga4"),
                ParsedDestination(platform="google_ads"),
                ParsedDestination(platform="meta", dest_event_name="Purchase"),
            ],
        ),
        ParsedEvent(
            name="refund",
            purpose="Order was refunded (full or partial). Corrects revenue reporting and measures return rates.",
            trigger_type="custom",
            trigger_config={"configuration": "Server-side or admin-triggered refund event"},
            status="planned",
            parameters=[
                ParsedParameter(name="transaction_id", type="string", required=True, example="T-12345"),
                ParsedParameter(name="currency", type="string", required=True, example="USD"),
                ParsedParameter(name="value", type="number", required=True, example="49.99"),
                ParsedParameter(name="items", type="array", required=False),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
    ]


# ---------------------------------------------------------------------------
# SaaS
# ---------------------------------------------------------------------------


def _saas_template() -> list[ParsedEvent]:
    return [
        ParsedEvent(
            name="sign_up",
            purpose="New user created an account. Top-of-funnel conversion for user acquisition.",
            trigger_type="form_submit",
            trigger_config={"configuration": "Registration form submission"},
            status="planned",
            parameters=[
                ParsedParameter(name="method", type="string", required=False, example="email"),
            ],
            destinations=[ParsedDestination(platform="ga4"), ParsedDestination(platform="google_ads")],
        ),
        ParsedEvent(
            name="login",
            purpose="Existing user logged in. Measures retention and engagement.",
            trigger_type="form_submit",
            trigger_config={"configuration": "Login form submission"},
            status="planned",
            parameters=[
                ParsedParameter(name="method", type="string", required=False, example="google_sso"),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="trial_start",
            purpose="User started a free trial. Key conversion event for freemium/trial-to-paid funnels.",
            trigger_type="datalayer_event",
            trigger_config={"configuration": "trial_start event after plan selection"},
            status="planned",
            parameters=[
                ParsedParameter(name="trial_type", type="string", required=False, example="14_day"),
                ParsedParameter(name="plan_name", type="string", required=False, example="pro"),
            ],
            destinations=[ParsedDestination(platform="ga4"), ParsedDestination(platform="google_ads")],
        ),
        ParsedEvent(
            name="subscribe",
            purpose="User converted to a paid subscription. Primary revenue event.",
            trigger_type="datalayer_event",
            trigger_config={"configuration": "Successful payment confirmation"},
            status="planned",
            parameters=[
                ParsedParameter(name="plan_name", type="string", required=True, example="pro"),
                ParsedParameter(name="value", type="number", required=True, example="25.00"),
                ParsedParameter(name="currency", type="string", required=True, example="USD"),
                ParsedParameter(name="billing_cycle", type="string", required=False, example="monthly"),
            ],
            destinations=[ParsedDestination(platform="ga4"), ParsedDestination(platform="google_ads")],
        ),
        ParsedEvent(
            name="cancel_subscription",
            purpose="User cancelled their paid subscription. Measures churn.",
            trigger_type="custom",
            trigger_config={"configuration": "Cancellation confirmation"},
            status="planned",
            parameters=[
                ParsedParameter(name="plan_name", type="string", required=True, example="pro"),
                ParsedParameter(name="reason", type="string", required=False, example="too_expensive"),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="feature_used",
            purpose="User engaged with a key product feature. Measures feature adoption and engagement depth.",
            trigger_type="click",
            trigger_config={"configuration": "Feature-specific triggers (varies per feature)"},
            status="planned",
            parameters=[
                ParsedParameter(name="feature_name", type="string", required=True, example="export_csv"),
                ParsedParameter(name="feature_category", type="string", required=False, example="data_tools"),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
    ]


# ---------------------------------------------------------------------------
# Lead Generation
# ---------------------------------------------------------------------------


def _lead_gen_template() -> list[ParsedEvent]:
    return [
        ParsedEvent(
            name="form_view",
            purpose="User viewed a lead capture form. Measures form exposure and top of lead funnel.",
            trigger_type="pageview",
            trigger_config={"configuration": "Pages containing lead forms"},
            status="planned",
            parameters=[
                ParsedParameter(name="form_id", type="string", required=True, example="contact_us"),
                ParsedParameter(name="form_name", type="string", required=False, example="Contact Us"),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="form_start",
            purpose="User began interacting with a lead form. Measures intent to submit.",
            trigger_type="click",
            trigger_config={"configuration": "First field interaction in a lead form"},
            status="planned",
            parameters=[
                ParsedParameter(name="form_id", type="string", required=True, example="contact_us"),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="form_submit",
            purpose="User submitted a lead form. Primary conversion event for lead generation.",
            trigger_type="form_submit",
            trigger_config={"configuration": "Form submission + server-side validation success"},
            status="planned",
            parameters=[
                ParsedParameter(name="form_id", type="string", required=True, example="contact_us"),
                ParsedParameter(name="form_name", type="string", required=False, example="Contact Us"),
                ParsedParameter(name="lead_type", type="string", required=False, example="demo_request"),
            ],
            destinations=[
                ParsedDestination(platform="ga4"),
                ParsedDestination(platform="google_ads"),
                ParsedDestination(platform="meta", dest_event_name="Lead"),
            ],
        ),
        ParsedEvent(
            name="lead_qualified",
            purpose="Lead met qualification criteria (MQL/SQL). Measures lead quality downstream.",
            trigger_type="custom",
            trigger_config={"configuration": "CRM webhook or server-side qualification event"},
            status="planned",
            parameters=[
                ParsedParameter(name="lead_id", type="string", required=True),
                ParsedParameter(name="qualification_type", type="string", required=False, example="mql"),
                ParsedParameter(name="score", type="number", required=False, example="85"),
            ],
            destinations=[ParsedDestination(platform="ga4"), ParsedDestination(platform="google_ads")],
        ),
    ]


# ---------------------------------------------------------------------------
# Media / Content
# ---------------------------------------------------------------------------


def _media_template() -> list[ParsedEvent]:
    return [
        ParsedEvent(
            name="content_view",
            purpose="User viewed an article/content page. Core engagement metric for media properties.",
            trigger_type="pageview",
            trigger_config={"configuration": "Article/content pages"},
            status="planned",
            parameters=[
                ParsedParameter(name="content_type", type="string", required=False, example="article"),
                ParsedParameter(name="content_id", type="string", required=True),
                ParsedParameter(name="author", type="string", required=False),
                ParsedParameter(name="category", type="string", required=False, example="technology"),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="scroll_depth",
            purpose="User scrolled to a significant depth on a content page. Measures read engagement vs bounce.",
            trigger_type="scroll",
            trigger_config={"configuration": "25%, 50%, 75%, 100% scroll thresholds"},
            status="planned",
            parameters=[
                ParsedParameter(name="percent_scrolled", type="number", required=True, example="75"),
                ParsedParameter(name="content_id", type="string", required=True),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="video_play",
            purpose="User started playing a video. Measures video engagement.",
            trigger_type="click",
            trigger_config={"configuration": "Video player play button or autoplay trigger"},
            status="planned",
            parameters=[
                ParsedParameter(name="video_title", type="string", required=True),
                ParsedParameter(name="video_url", type="string", required=False),
                ParsedParameter(name="video_duration", type="number", required=False, example="120"),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="subscribe",
            purpose="User subscribed to newsletter or content updates. Key retention conversion.",
            trigger_type="form_submit",
            trigger_config={"configuration": "Newsletter signup form submission"},
            status="planned",
            parameters=[
                ParsedParameter(
                    name="subscription_type", type="string", required=False, example="newsletter"
                ),
            ],
            destinations=[
                ParsedDestination(platform="ga4"),
                ParsedDestination(platform="meta", dest_event_name="Subscribe"),
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# App (mobile/web app)
# ---------------------------------------------------------------------------


def _app_template() -> list[ParsedEvent]:
    return [
        ParsedEvent(
            name="app_open",
            purpose="User opened the app. Measures daily/monthly active users.",
            trigger_type="custom",
            trigger_config={"configuration": "App launch or foreground event"},
            status="planned",
            parameters=[
                ParsedParameter(name="source", type="string", required=False, example="deeplink"),
            ],
            destinations=[ParsedDestination(platform="ga4")],
        ),
        ParsedEvent(
            name="sign_up",
            purpose="New user completed registration. Primary acquisition event.",
            trigger_type="form_submit",
            trigger_config={"configuration": "Registration completion"},
            status="planned",
            parameters=[
                ParsedParameter(name="method", type="string", required=False, example="email"),
            ],
            destinations=[ParsedDestination(platform="ga4"), ParsedDestination(platform="google_ads")],
        ),
        ParsedEvent(
            name="purchase",
            purpose="In-app purchase completed. Primary revenue event.",
            trigger_type="datalayer_event",
            trigger_config={"configuration": "Payment confirmation callback"},
            status="planned",
            parameters=[
                ParsedParameter(name="transaction_id", type="string", required=True),
                ParsedParameter(name="value", type="number", required=True, example="9.99"),
                ParsedParameter(name="currency", type="string", required=True, example="USD"),
                ParsedParameter(name="item_name", type="string", required=False, example="premium_monthly"),
            ],
            destinations=[ParsedDestination(platform="ga4"), ParsedDestination(platform="google_ads")],
        ),
        ParsedEvent(
            name="app_remove",
            purpose="User uninstalled the app. Measures churn at the app level.",
            trigger_type="custom",
            trigger_config={"configuration": "Firebase app_remove event (server-side)"},
            status="planned",
            parameters=[],
            destinations=[ParsedDestination(platform="ga4")],
        ),
    ]
