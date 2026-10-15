<?php
/**
 * Fajaede child theme (parent: Twenty Twenty-Four).
 *
 * @package Fajaede
 */

defined( 'ABSPATH' ) || exit;

define( 'FAJAEDE_VERSION', '0.1.0' );
define( 'FAJAEDE_DIR', get_stylesheet_directory() );
define( 'FAJAEDE_URI', get_stylesheet_directory_uri() );

require_once FAJAEDE_DIR . '/inc/settings.php';
require_once FAJAEDE_DIR . '/inc/recaptcha.php';
require_once FAJAEDE_DIR . '/inc/contact-form.php';
require_once FAJAEDE_DIR . '/inc/download-shortcode.php';

/**
 * Theme setup.
 */
function fajaede_setup() {
	load_child_theme_textdomain( 'fajaede', FAJAEDE_DIR . '/languages' );
	add_editor_style( 'assets/css/components.css' );
}
add_action( 'after_setup_theme', 'fajaede_setup' );

/**
 * Front-end styles. TT4 laadt zelf geen style.css van het child-thema,
 * daarom laden we alleen onze componenten-CSS.
 */
function fajaede_enqueue_assets() {
	wp_enqueue_style(
		'fajaede-components',
		FAJAEDE_URI . '/assets/css/components.css',
		array(),
		FAJAEDE_VERSION
	);
}
add_action( 'wp_enqueue_scripts', 'fajaede_enqueue_assets' );

/**
 * Eigen patterncategorie zodat de bedrijfsblokken bij elkaar staan in de editor.
 */
function fajaede_register_pattern_category() {
	register_block_pattern_category(
		'fajaede',
		array( 'label' => __( 'Fajaede', 'fajaede' ) )
	);
}
add_action( 'init', 'fajaede_register_pattern_category' );
