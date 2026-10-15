<?php
/**
 * Google reCAPTCHA v3: script laden en tokens server-side verifiëren.
 *
 * Het script wordt alleen geladen op pagina's waar een formulier staat
 * (de shortcode roept fajaede_recaptcha_enqueue() aan), zodat de rest van
 * de site geen Google-requests en geen cookie-melding nodig heeft.
 *
 * @package Fajaede
 */

defined( 'ABSPATH' ) || exit;

/**
 * Is reCAPTCHA geconfigureerd?
 *
 * @return bool
 */
function fajaede_recaptcha_enabled() {
	return '' !== fajaede_get_setting( 'recaptcha_site_key' )
		&& '' !== fajaede_get_setting( 'recaptcha_secret_key' );
}

/**
 * Laad het reCAPTCHA v3 script plus onze form-handler.
 */
function fajaede_recaptcha_enqueue() {
	$site_key = fajaede_get_setting( 'recaptcha_site_key' );

	if ( '' !== $site_key ) {
		wp_enqueue_script(
			'google-recaptcha',
			'https://www.google.com/recaptcha/api.js?render=' . rawurlencode( $site_key ),
			array(),
			null, // phpcs:ignore WordPress.WP.EnqueuedResourceParameters.MissingVersion -- Google beheert de versie.
			true
		);
	}

	wp_enqueue_script(
		'fajaede-forms',
		FAJAEDE_URI . '/assets/js/forms.js',
		'' !== $site_key ? array( 'google-recaptcha' ) : array(),
		FAJAEDE_VERSION,
		true
	);

	wp_localize_script(
		'fajaede-forms',
		'fajaedeRecaptcha',
		array( 'siteKey' => $site_key )
	);
}

/**
 * Verifieer een reCAPTCHA v3-token bij Google.
 *
 * @param string $token           Token uit het formulier.
 * @param string $expected_action Action-naam die de front-end meegaf.
 * @return true|WP_Error
 */
function fajaede_recaptcha_verify( $token, $expected_action ) {
	if ( ! fajaede_recaptcha_enabled() ) {
		// Niet geconfigureerd: alleen toestaan in een ontwikkelomgeving.
		return 'production' === wp_get_environment_type()
			? new WP_Error( 'recaptcha_not_configured', __( 'reCAPTCHA is niet ingesteld.', 'fajaede' ) )
			: true;
	}

	if ( '' === $token ) {
		return new WP_Error( 'recaptcha_missing', __( 'Beveiligingscontrole ontbreekt.', 'fajaede' ) );
	}

	$response = wp_remote_post(
		'https://www.google.com/recaptcha/api/siteverify',
		array(
			'timeout' => 10,
			'body'    => array(
				'secret'   => fajaede_get_setting( 'recaptcha_secret_key' ),
				'response' => $token,
				'remoteip' => isset( $_SERVER['REMOTE_ADDR'] ) ? sanitize_text_field( wp_unslash( $_SERVER['REMOTE_ADDR'] ) ) : '',
			),
		)
	);

	if ( is_wp_error( $response ) ) {
		return new WP_Error( 'recaptcha_unreachable', __( 'Beveiligingscontrole kon niet worden uitgevoerd.', 'fajaede' ) );
	}

	$data = json_decode( wp_remote_retrieve_body( $response ), true );

	if ( empty( $data['success'] ) ) {
		return new WP_Error( 'recaptcha_failed', __( 'Beveiligingscontrole mislukt.', 'fajaede' ) );
	}

	if ( ! isset( $data['action'] ) || $data['action'] !== $expected_action ) {
		return new WP_Error( 'recaptcha_action', __( 'Beveiligingscontrole mislukt.', 'fajaede' ) );
	}

	$site_host = wp_parse_url( home_url(), PHP_URL_HOST );
	if ( isset( $data['hostname'] ) && $site_host && strcasecmp( $data['hostname'], $site_host ) !== 0 ) {
		return new WP_Error( 'recaptcha_hostname', __( 'Beveiligingscontrole mislukt.', 'fajaede' ) );
	}

	$score     = isset( $data['score'] ) ? (float) $data['score'] : 0.0;
	$threshold = (float) fajaede_get_setting( 'recaptcha_threshold' );

	/**
	 * Pas de minimale score per formulier aan.
	 *
	 * @param float  $threshold       Standaarddrempel uit de instellingen.
	 * @param string $expected_action Action-naam van het formulier.
	 */
	$threshold = (float) apply_filters( 'fajaede_recaptcha_threshold', $threshold, $expected_action );

	if ( $score < $threshold ) {
		return new WP_Error( 'recaptcha_score', __( 'Je bericht is als mogelijke spam aangemerkt.', 'fajaede' ) );
	}

	return true;
}
