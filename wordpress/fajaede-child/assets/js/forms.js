/**
 * reCAPTCHA v3 voor Fajaede-formulieren.
 *
 * Tokens zijn 2 minuten geldig, dus we halen er pas één op bij het verzenden.
 */
( function () {
	'use strict';

	var config = window.fajaedeRecaptcha || {};

	document.querySelectorAll( 'form[data-recaptcha-action]' ).forEach( function ( form ) {
		form.addEventListener( 'submit', function ( event ) {
			if ( form.dataset.recaptchaDone === '1' ) {
				return; // Tweede ronde: token staat erin, normaal verzenden.
			}

			// Zonder site key (ontwikkelomgeving) gewoon verzenden.
			if ( ! config.siteKey || typeof window.grecaptcha === 'undefined' ) {
				return;
			}

			event.preventDefault();

			var button = form.querySelector( '[type="submit"]' );
			if ( button ) {
				button.disabled = true;
			}

			window.grecaptcha.ready( function () {
				window.grecaptcha
					.execute( config.siteKey, { action: form.dataset.recaptchaAction } )
					.then( function ( token ) {
						form.querySelector( 'input[name="recaptcha_token"]' ).value = token;
						form.dataset.recaptchaDone = '1';
						form.submit();
					} )
					.catch( function () {
						if ( button ) {
							button.disabled = false;
						}
					} );
			} );
		} );
	} );
} )();
