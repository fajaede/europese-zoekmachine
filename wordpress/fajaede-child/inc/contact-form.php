<?php
/**
 * Contactformulier: [fajaede_contact]
 *
 * Attributen:
 *   title   Kop boven het formulier (optioneel).
 *   button  Tekst van de verzendknop. Standaard "Verstuur bericht".
 *   subject Onderwerpregel van de e-mail. Standaard "Nieuw bericht via fajaede.nl".
 *
 * Verwerking via admin-post.php met nonce, honeypot en reCAPTCHA v3.
 *
 * @package Fajaede
 */

defined( 'ABSPATH' ) || exit;

const FAJAEDE_CONTACT_ACTION = 'fajaede_contact';

/**
 * Render het contactformulier.
 *
 * @param array $atts Shortcode-attributen.
 * @return string
 */
function fajaede_contact_shortcode( $atts ) {
	$atts = shortcode_atts(
		array(
			'title'   => '',
			'button'  => __( 'Verstuur bericht', 'fajaede' ),
			'subject' => __( 'Nieuw bericht via fajaede.nl', 'fajaede' ),
		),
		$atts,
		'fajaede_contact'
	);

	fajaede_recaptcha_enqueue();

	// phpcs:ignore WordPress.Security.NonceVerification.Recommended -- alleen weergave van status.
	$status = isset( $_GET['contact'] ) ? sanitize_key( wp_unslash( $_GET['contact'] ) ) : '';

	ob_start();
	?>
	<div class="fajaede-form-wrap">
		<?php if ( '' !== $atts['title'] ) : ?>
			<h2 class="fajaede-form__title"><?php echo esc_html( $atts['title'] ); ?></h2>
		<?php endif; ?>

		<?php if ( 'sent' === $status ) : ?>
			<p class="fajaede-notice fajaede-notice--success" role="status"><?php esc_html_e( 'Bedankt! We nemen zo snel mogelijk contact met je op.', 'fajaede' ); ?></p>
		<?php elseif ( 'error' === $status ) : ?>
			<p class="fajaede-notice fajaede-notice--error" role="alert"><?php esc_html_e( 'Er ging iets mis bij het versturen. Probeer het opnieuw of mail ons direct.', 'fajaede' ); ?></p>
		<?php elseif ( 'invalid' === $status ) : ?>
			<p class="fajaede-notice fajaede-notice--error" role="alert"><?php esc_html_e( 'Vul je naam, een geldig e-mailadres en een bericht in.', 'fajaede' ); ?></p>
		<?php endif; ?>

		<form class="fajaede-form" method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>" data-recaptcha-action="contact">
			<input type="hidden" name="action" value="<?php echo esc_attr( FAJAEDE_CONTACT_ACTION ); ?>" />
			<input type="hidden" name="recaptcha_token" value="" />
			<input type="hidden" name="subject" value="<?php echo esc_attr( $atts['subject'] ); ?>" />
			<input type="hidden" name="redirect_to" value="<?php echo esc_url( get_permalink() ); ?>" />
			<?php wp_nonce_field( FAJAEDE_CONTACT_ACTION, 'fajaede_nonce' ); ?>

			<p class="fajaede-form__hp" aria-hidden="true">
				<label>Website <input type="text" name="website" tabindex="-1" autocomplete="off" /></label>
			</p>

			<p class="fajaede-form__field">
				<label for="fajaede-name"><?php esc_html_e( 'Naam', 'fajaede' ); ?> *</label>
				<input id="fajaede-name" type="text" name="name" required autocomplete="name" maxlength="100" />
			</p>
			<p class="fajaede-form__field">
				<label for="fajaede-company"><?php esc_html_e( 'Bedrijf', 'fajaede' ); ?></label>
				<input id="fajaede-company" type="text" name="company" autocomplete="organization" maxlength="100" />
			</p>
			<p class="fajaede-form__field">
				<label for="fajaede-email"><?php esc_html_e( 'E-mail', 'fajaede' ); ?> *</label>
				<input id="fajaede-email" type="email" name="email" required autocomplete="email" maxlength="200" />
			</p>
			<p class="fajaede-form__field">
				<label for="fajaede-phone"><?php esc_html_e( 'Telefoon', 'fajaede' ); ?></label>
				<input id="fajaede-phone" type="tel" name="phone" autocomplete="tel" maxlength="30" />
			</p>
			<p class="fajaede-form__field fajaede-form__field--full">
				<label for="fajaede-message"><?php esc_html_e( 'Bericht', 'fajaede' ); ?> *</label>
				<textarea id="fajaede-message" name="message" rows="6" required maxlength="5000"></textarea>
			</p>
			<p class="fajaede-form__field fajaede-form__field--full fajaede-form__consent">
				<label>
					<input type="checkbox" name="consent" value="1" required />
					<?php
					printf(
						/* translators: %s: link naar privacyverklaring */
						esc_html__( 'Ik ga akkoord met de %s.', 'fajaede' ),
						'<a href="' . esc_url( get_privacy_policy_url() ) . '">' . esc_html__( 'privacyverklaring', 'fajaede' ) . '</a>'
					);
					?>
				</label>
			</p>

			<p class="fajaede-form__actions">
				<button type="submit" class="wp-element-button"><?php echo esc_html( $atts['button'] ); ?></button>
			</p>
			<p class="fajaede-form__legal">
				<?php
				// Verplicht als de reCAPTCHA-badge verborgen wordt (zie components.css).
				printf(
					/* translators: 1: privacy-link Google, 2: voorwaarden-link Google */
					esc_html__( 'Deze site is beveiligd met reCAPTCHA; het %1$s en de %2$s van Google zijn van toepassing.', 'fajaede' ),
					'<a href="https://policies.google.com/privacy" rel="noopener" target="_blank">' . esc_html__( 'privacybeleid', 'fajaede' ) . '</a>',
					'<a href="https://policies.google.com/terms" rel="noopener" target="_blank">' . esc_html__( 'servicevoorwaarden', 'fajaede' ) . '</a>'
				);
				?>
			</p>
		</form>
	</div>
	<?php
	return ob_get_clean();
}
add_shortcode( 'fajaede_contact', 'fajaede_contact_shortcode' );

/**
 * Verwerk een inzending.
 */
function fajaede_handle_contact() {
	$redirect = isset( $_POST['redirect_to'] ) ? esc_url_raw( wp_unslash( $_POST['redirect_to'] ) ) : home_url( '/' );
	$redirect = wp_validate_redirect( $redirect, home_url( '/' ) );

	$back = static function ( $status ) use ( $redirect ) {
		wp_safe_redirect( add_query_arg( 'contact', $status, $redirect ) . '#contact' );
		exit;
	};

	if ( ! isset( $_POST['fajaede_nonce'] ) || ! wp_verify_nonce( sanitize_text_field( wp_unslash( $_POST['fajaede_nonce'] ) ), FAJAEDE_CONTACT_ACTION ) ) {
		$back( 'error' );
	}

	// Honeypot ingevuld = bot; doe alsof het gelukt is.
	if ( ! empty( $_POST['website'] ) ) {
		$back( 'sent' );
	}

	$token  = isset( $_POST['recaptcha_token'] ) ? sanitize_text_field( wp_unslash( $_POST['recaptcha_token'] ) ) : '';
	$verify = fajaede_recaptcha_verify( $token, 'contact' );
	if ( is_wp_error( $verify ) ) {
		$back( 'error' );
	}

	$name    = isset( $_POST['name'] ) ? sanitize_text_field( wp_unslash( $_POST['name'] ) ) : '';
	$company = isset( $_POST['company'] ) ? sanitize_text_field( wp_unslash( $_POST['company'] ) ) : '';
	$email   = isset( $_POST['email'] ) ? sanitize_email( wp_unslash( $_POST['email'] ) ) : '';
	$phone   = isset( $_POST['phone'] ) ? sanitize_text_field( wp_unslash( $_POST['phone'] ) ) : '';
	$message = isset( $_POST['message'] ) ? sanitize_textarea_field( wp_unslash( $_POST['message'] ) ) : '';
	$subject = isset( $_POST['subject'] ) ? sanitize_text_field( wp_unslash( $_POST['subject'] ) ) : '';
	$consent = ! empty( $_POST['consent'] );

	if ( '' === $name || ! is_email( $email ) || '' === $message || ! $consent ) {
		$back( 'invalid' );
	}

	$body = implode(
		"\n",
		array(
			sprintf( 'Naam: %s', $name ),
			sprintf( 'Bedrijf: %s', $company ),
			sprintf( 'E-mail: %s', $email ),
			sprintf( 'Telefoon: %s', $phone ),
			'',
			$message,
		)
	);

	$headers = array( 'Reply-To: ' . $name . ' <' . $email . '>' );
	$to      = fajaede_get_setting( 'contact_recipient' );
	$subject = '' !== $subject ? $subject : __( 'Nieuw bericht via fajaede.nl', 'fajaede' );

	$sent = wp_mail( $to, $subject, $body, $headers );

	/**
	 * Na een geldige inzending, bv. om naar een CRM te sturen.
	 *
	 * @param array $fields Gesaniteerde velden.
	 * @param bool  $sent   Of wp_mail slaagde.
	 */
	do_action( 'fajaede_contact_submitted', compact( 'name', 'company', 'email', 'phone', 'message' ), $sent );

	$back( $sent ? 'sent' : 'error' );
}
add_action( 'admin_post_nopriv_' . FAJAEDE_CONTACT_ACTION, 'fajaede_handle_contact' );
add_action( 'admin_post_' . FAJAEDE_CONTACT_ACTION, 'fajaede_handle_contact' );
