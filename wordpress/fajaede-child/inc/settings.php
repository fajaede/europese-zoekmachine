<?php
/**
 * Instellingen: reCAPTCHA-sleutels en ontvanger contactformulier.
 *
 * Sleutels kunnen in wp-config.php als constante worden gezet (aanbevolen,
 * blijven dan buiten de database):
 *   define( 'FAJAEDE_RECAPTCHA_SITE_KEY', '...' );
 *   define( 'FAJAEDE_RECAPTCHA_SECRET_KEY', '...' );
 * Anders via Instellingen > Fajaede.
 *
 * @package Fajaede
 */

defined( 'ABSPATH' ) || exit;

/**
 * Lees een instelling; constante in wp-config gaat voor op de optie.
 *
 * @param string $key Naam van de instelling.
 * @return string
 */
function fajaede_get_setting( $key ) {
	$constants = array(
		'recaptcha_site_key'   => 'FAJAEDE_RECAPTCHA_SITE_KEY',
		'recaptcha_secret_key' => 'FAJAEDE_RECAPTCHA_SECRET_KEY',
	);

	if ( isset( $constants[ $key ] ) && defined( $constants[ $key ] ) ) {
		return (string) constant( $constants[ $key ] );
	}

	$defaults = array(
		'recaptcha_threshold' => '0.5',
		'contact_recipient'   => get_option( 'admin_email' ),
	);

	$options = get_option( 'fajaede_settings', array() );
	if ( isset( $options[ $key ] ) && '' !== $options[ $key ] ) {
		return (string) $options[ $key ];
	}

	return isset( $defaults[ $key ] ) ? (string) $defaults[ $key ] : '';
}

/**
 * Registreer de instellingenpagina.
 */
function fajaede_register_settings() {
	register_setting(
		'fajaede_settings',
		'fajaede_settings',
		array(
			'type'              => 'array',
			'sanitize_callback' => 'fajaede_sanitize_settings',
			'default'           => array(),
		)
	);

	add_settings_section( 'fajaede_recaptcha', __( 'reCAPTCHA v3', 'fajaede' ), '__return_false', 'fajaede' );
	add_settings_section( 'fajaede_contact', __( 'Contactformulier', 'fajaede' ), '__return_false', 'fajaede' );

	$fields = array(
		'recaptcha_site_key'   => array( __( 'Site key', 'fajaede' ), 'fajaede_recaptcha', 'text' ),
		'recaptcha_secret_key' => array( __( 'Secret key', 'fajaede' ), 'fajaede_recaptcha', 'password' ),
		'recaptcha_threshold'  => array( __( 'Minimale score (0.0–1.0)', 'fajaede' ), 'fajaede_recaptcha', 'number' ),
		'contact_recipient'    => array( __( 'Ontvanger e-mail', 'fajaede' ), 'fajaede_contact', 'email' ),
	);

	foreach ( $fields as $key => $field ) {
		add_settings_field(
			$key,
			$field[0],
			'fajaede_render_setting_field',
			'fajaede',
			$field[1],
			array(
				'key'  => $key,
				'type' => $field[2],
			)
		);
	}
}
add_action( 'admin_init', 'fajaede_register_settings' );

/**
 * Sanitize de opgeslagen instellingen.
 *
 * @param mixed $input Ruwe invoer.
 * @return array
 */
function fajaede_sanitize_settings( $input ) {
	$input = is_array( $input ) ? $input : array();
	$out   = array();

	$out['recaptcha_site_key']   = isset( $input['recaptcha_site_key'] ) ? sanitize_text_field( $input['recaptcha_site_key'] ) : '';
	$out['recaptcha_secret_key'] = isset( $input['recaptcha_secret_key'] ) ? sanitize_text_field( $input['recaptcha_secret_key'] ) : '';

	$threshold                  = isset( $input['recaptcha_threshold'] ) ? (float) $input['recaptcha_threshold'] : 0.5;
	$out['recaptcha_threshold'] = (string) min( 1, max( 0, $threshold ) );

	$out['contact_recipient'] = isset( $input['contact_recipient'] ) ? sanitize_email( $input['contact_recipient'] ) : '';

	return $out;
}

/**
 * Render één instellingenveld.
 *
 * @param array $args Veldargumenten.
 */
function fajaede_render_setting_field( $args ) {
	$key     = $args['key'];
	$options = get_option( 'fajaede_settings', array() );
	$value   = isset( $options[ $key ] ) ? $options[ $key ] : '';
	$locked  = in_array( $key, array( 'recaptcha_site_key', 'recaptcha_secret_key' ), true )
		&& defined( 'FAJAEDE_' . strtoupper( $key ) );

	if ( $locked ) {
		echo '<em>' . esc_html__( 'Ingesteld via wp-config.php', 'fajaede' ) . '</em>';
		return;
	}

	printf(
		'<input type="%1$s" name="fajaede_settings[%2$s]" value="%3$s" class="regular-text" %4$s />',
		esc_attr( $args['type'] ),
		esc_attr( $key ),
		esc_attr( $value ),
		'number' === $args['type'] ? 'min="0" max="1" step="0.1"' : ''
	);
}

/**
 * Menu-item onder Instellingen.
 */
function fajaede_settings_menu() {
	add_options_page(
		__( 'Fajaede', 'fajaede' ),
		__( 'Fajaede', 'fajaede' ),
		'manage_options',
		'fajaede',
		'fajaede_render_settings_page'
	);
}
add_action( 'admin_menu', 'fajaede_settings_menu' );

/**
 * Render de instellingenpagina.
 */
function fajaede_render_settings_page() {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}
	?>
	<div class="wrap">
		<h1><?php esc_html_e( 'Fajaede instellingen', 'fajaede' ); ?></h1>
		<form action="options.php" method="post">
			<?php
			settings_fields( 'fajaede_settings' );
			do_settings_sections( 'fajaede' );
			submit_button();
			?>
		</form>
	</div>
	<?php
}
