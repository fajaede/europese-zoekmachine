<?php
/**
 * Title: Diensten (3 kolommen)
 * Slug: fajaede/services
 * Categories: fajaede, services
 * Description: Drie diensten naast elkaar met korte uitleg.
 */

$fajaede_services = array(
	array( __( 'Europese zoekmachine', 'fajaede' ), __( 'Zoeken zonder tracking, met resultaten uit Europese bronnen.', 'fajaede' ) ),
	array( __( 'SEO & GEO-audits', 'fajaede' ), __( 'Inzicht in hoe zoekmachines én AI-assistenten je site zien.', 'fajaede' ) ),
	array( __( 'Developer API', 'fajaede' ), __( 'Genereer SEO-metadata automatisch vanuit je eigen platform.', 'fajaede' ) ),
);
?>
<!-- wp:group {"align":"full","style":{"spacing":{"padding":{"top":"var:preset|spacing|60","bottom":"var:preset|spacing|60","left":"var:preset|spacing|50","right":"var:preset|spacing|50"}}},"layout":{"type":"constrained"}} -->
<div class="wp-block-group alignfull" style="padding-top:var(--wp--preset--spacing--60);padding-right:var(--wp--preset--spacing--50);padding-bottom:var(--wp--preset--spacing--60);padding-left:var(--wp--preset--spacing--50)">
	<!-- wp:heading {"textAlign":"center"} -->
	<h2 class="wp-block-heading has-text-align-center"><?php esc_html_e( 'Wat we doen', 'fajaede' ); ?></h2>
	<!-- /wp:heading -->
	<!-- wp:columns {"align":"wide"} -->
	<div class="wp-block-columns alignwide">
		<?php foreach ( $fajaede_services as $fajaede_service ) : ?>
		<!-- wp:column {"style":{"spacing":{"padding":{"top":"var:preset|spacing|30","bottom":"var:preset|spacing|30","left":"var:preset|spacing|30","right":"var:preset|spacing|30"}},"border":{"radius":"12px"}},"backgroundColor":"base-2"} -->
		<div class="wp-block-column has-base-2-background-color has-background" style="border-radius:12px;padding-top:var(--wp--preset--spacing--30);padding-right:var(--wp--preset--spacing--30);padding-bottom:var(--wp--preset--spacing--30);padding-left:var(--wp--preset--spacing--30)">
			<!-- wp:heading {"level":3,"fontSize":"large"} -->
			<h3 class="wp-block-heading has-large-font-size"><?php echo esc_html( $fajaede_service[0] ); ?></h3>
			<!-- /wp:heading -->
			<!-- wp:paragraph -->
			<p><?php echo esc_html( $fajaede_service[1] ); ?></p>
			<!-- /wp:paragraph -->
		</div>
		<!-- /wp:column -->
		<?php endforeach; ?>
	</div>
	<!-- /wp:columns -->
</div>
<!-- /wp:group -->
