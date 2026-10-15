<?php
/**
 * Title: Downloadpagina (startpunt)
 * Slug: fajaede/download-page
 * Categories: fajaede
 * Description: Hero-download plus raster met extra bestanden. Vervang de file-ID's.
 * Post Types: page
 * Template Types: page-download
 */
?>
<!-- wp:shortcode -->
[fajaede_download file="0" layout="hero" title="Productnaam" subtitle="Korte belofte in één zin" version="1.0" button="Download nu" note="Gratis · geen account nodig"]
Wat de bezoeker krijgt en waarom het waardevol is.
[/fajaede_download]
<!-- /wp:shortcode -->

<!-- wp:heading -->
<h2 class="wp-block-heading"><?php esc_html_e( 'Meer downloads', 'fajaede' ); ?></h2>
<!-- /wp:heading -->

<!-- wp:shortcode -->
[fajaede_downloads columns="3"]
[fajaede_download file="0" title="Handleiding"]PDF met installatie en eerste stappen.[/fajaede_download]
[fajaede_download file="0" title="Brochure"]Overzicht van diensten en tarieven.[/fajaede_download]
[fajaede_download file="0" title="Whitepaper"]Achtergrond en onderzoek.[/fajaede_download]
[/fajaede_downloads]
<!-- /wp:shortcode -->
