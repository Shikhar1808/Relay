#!/bin/sh
# Turns a bare WordPress into a WooCommerce store the MCP server can call.
# Safe to run again, and it is, on every start. What .env holds (the admin's password, the
# REST key) is applied each time; the store's own settings are set once and then left alone.
# Runs in the wordpress:cli image on WordPress's own volume (docker-compose.yml: wp-init).
#
# Needs the WORDPRESS_DB_* variables of the wordpress image (its wp-config.php reads them),
# and WP_URL, WP_TITLE, WP_ADMIN_USER, WP_ADMIN_PASSWORD, WP_ADMIN_EMAIL,
# WOOCOMMERCE_KEY, WOOCOMMERCE_SECRET. Optional: WP_TIMEZONE.
set -eu
export WP_CLI_CACHE_DIR=/tmp/wp-cli-cache  # this runs as WordPress's uid, which has no home directory here

if ! wp core is-installed; then
    # Installed with a password wp-cli makes up (its output, which prints it, is dropped);
    # the real one is set just below.
    wp core install --url="$WP_URL" --title="$WP_TITLE" --admin_user="$WP_ADMIN_USER" \
        --admin_email="$WP_ADMIN_EMAIL" --skip-email >/dev/null
    echo "wp-init: WordPress installed"
fi
# The admin's password follows .env, read inside PHP: never an argument, never a log line.
# Set only when it differs, because setting it signs the admin out of wp-admin.
wp eval '$admin = get_user_by("login", getenv("WP_ADMIN_USER"));
if ($admin && !wp_check_password(getenv("WP_ADMIN_PASSWORD"), $admin->user_pass, $admin->ID)) {
    wp_set_password(getenv("WP_ADMIN_PASSWORD"), $admin->ID);
}'
# Pinned: the REST key below is written straight into WooCommerce's own table, whose layout a later release may change.
wp plugin is-active woocommerce || wp plugin install woocommerce --version=11.2.1 --activate
# /wp-json/, which the MCP server calls, needs pretty permalinks (the wordpress image already ships
# the rewrite rules). Only "Plain" is put right: any other structure the admin picked stays.
[ -n "$(wp option get permalink_structure)" ] || wp rewrite structure '/%postname%/'

# The store's own settings, set once. From then on they are the store admin's: a later start
# must not undo what was changed in wp-admin. The mark is written last, so a run that failed
# halfway does all of it again.
if [ "$(wp option get urumi_store_set_up 2>/dev/null)" != yes ]; then
    # A fresh WooCommerce hides the storefront behind a "coming soon" page.
    wp option update woocommerce_coming_soon no
    # No shipping zones are set up, and without one checkout would have no way to deliver an order.
    wp option update woocommerce_ship_to_countries disabled
    # Skip the setup wizard that wp-admin would otherwise open on the first visit.
    wp option update woocommerce_onboarding_profile '{"skipped":true}' --format=json
    # "Today's orders" means the store's day: see list_orders in app/main.py.
    [ -z "${WP_TIMEZONE:-}" ] || wp option update timezone_string "$WP_TIMEZONE"

    wp wc tool run install_pages --user="$WP_ADMIN_USER"  # shop, cart, checkout, my account
    wp wc payment_gateway update cod --enabled=true --user="$WP_ADMIN_USER"

    if [ "$(wp post list --post_type=product --format=count)" = 0 ]; then
        wp wc product create --name="Sample Mug" --type=simple --regular_price=199 --user="$WP_ADMIN_USER" --porcelain
    fi
    wp option update urumi_store_set_up yes
fi

# The REST key belongs to a Shop manager, not the administrator: it can change products, orders
# and WooCommerce settings, not plugins, themes or administrator accounts. Nobody signs in as this user.
wp user get mcp --field=ID >/dev/null 2>&1 || wp user create mcp mcp@example.invalid --role=shop_manager --porcelain >/dev/null

wp eval-file - <<'PHP'
<?php
// The pair is made outside WordPress so that the MCP server can be handed the same two
// values. WooCommerce keeps the key hashed and finds a caller by that hash
// (WC_REST_Authentication), which is exactly what its own "Add key" screen stores.
global $wpdb;
$key = getenv('WOOCOMMERCE_KEY');
$secret = getenv('WOOCOMMERCE_SECRET');
// The secret column is 43 characters wide: anything longer would be cut and never match.
if (!preg_match('/^ck_[0-9a-f]{40}$/', $key) || !preg_match('/^cs_[0-9a-f]{40}$/', $secret)) {
    WP_CLI::error('WOOCOMMERCE_KEY must be ck_ and WOOCOMMERCE_SECRET cs_, each followed by 40 hex characters');
}
$table = $wpdb->prefix . 'woocommerce_api_keys';
$wpdb->delete($table, ['description' => 'urumi-mcp']);  // so a changed pair replaces the old one
$stored = $wpdb->insert($table, [
    'user_id' => get_user_by('login', 'mcp')->ID,
    'description' => 'urumi-mcp',
    'permissions' => 'read_write',
    'consumer_key' => wc_api_hash($key),
    'consumer_secret' => $secret,
    'truncated_key' => substr($key, -7),
]);
if (!$stored) {
    WP_CLI::error('could not store the REST key: ' . $wpdb->last_error);
}
WP_CLI::success('REST key registered for the user mcp');
PHP

echo "wp-init: the store at $WP_URL is ready"
