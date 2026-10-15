<?php
/**
 * Downloadpagina via shortcode.
 *
 * Eén download:
 *   [fajaede_download file="123" title="Fajaede SEO-rapport" version="2.1"
 *        layout="hero" button="Download PDF" image="456" note="Gratis, geen account nodig"]
 *   Korte beschrijving van de download. Mag <strong>HTML</strong> en andere shortcodes bevatten.
 *   [/fajaede_download]
 *
 * Meerdere downloads in een raster:
 *   [fajaede_downloads columns="3"]
 *     [fajaede_download file="123" title="Windows"]…[/fajaede_download]
 *     [fajaede_download file="124" title="macOS"]…[/fajaede_download]
 *   [/fajaede_downloads]
 *
 * Attributen [fajaede_download]:
 *   file      Mediabibliotheek-ID (aanbevolen: grootte, type en teller automatisch) of volledige URL.
 *   title     Titel. Standaard de titel van het bestand.
 *   subtitle  Regel onder de titel.
 *   version   Versienummer, getoond als label.
 *   date      Datum (vrij tekstveld). Standaard de uploaddatum bij een ID.
 *   button    Knoptekst. Standaard "Download".
 *   image     Mediabibliotheek-ID of URL van een afbeelding/cover.
 *   note      Kleine tekst onder de knop.
 *   layout    "card" (standaard) of "hero" (groot, voor een pagina met één download).
 *   meta      "yes"/"no": toon bestandstype, grootte, versie en datum. Standaard "yes".
 *   count     "yes" om het aantal downloads te tonen. Standaard "no".
 *
 * @package Fajaede
 */

defined( 'ABSPATH' ) || exit;

const FAJAEDE_DOWNLOAD_QUERY_VAR = 'fajaede_download';
const FAJAEDE_DOWNLOAD_COUNT_KEY = '_fajaede_download_count';

/**
 * Render één download.
 *
 * @param array       $atts    Attributen.
 * @param string|null $content Beschrijving.
 * @return string
 */
function fajaede_download_shortcode( $atts, $content = null ) {
	$atts = shortcode_atts(
		array(
			'file'     => '',
			'title'    => '',
			'subtitle' => '',
			'version'  => '',
			'date'     => '',
			'button'   => __( 'Download', 'fajaede' ),
			'image'    => '',
			'note'     => '',
			'layout'   => 'card',
			'meta'     => 'yes',
			'count'    => 'no',
		),
		$atts,
		'fajaede_download'
	);

	$file = fajaede_download_resolve_file( $atts['file'] );
	if ( ! $file ) {
		return current_user_can( 'edit_posts' )
			? '<p class="fajaede-notice fajaede-notice--error">' . esc_html__( 'fajaede_download: geen geldig bestand opgegeven (file="ID" of URL).', 'fajaede' ) . '</p>'
			: '';
	}

	$title  = '' !== $atts['title'] ? $atts['title'] : $file['title'];
	$date   = '' !== $atts['date'] ? $atts['date'] : $file['date'];
	$layout = in_array( $atts['layout'], array( 'card', 'hero' ), true ) ? $atts['layout'] : 'card';
	$image  = fajaede_download_image_html( $atts['image'], $title );

	$meta = array();
	if ( 'no' !== $atts['meta'] ) {
		if ( $file['type'] ) {
			$meta[] = strtoupper( $file['type'] );
		}
		if ( $file['size'] ) {
			$meta[] = size_format( $file['size'], 1 );
		}
		if ( '' !== $atts['version'] ) {
			/* translators: %s: versienummer */
			$meta[] = sprintf( __( 'Versie %s', 'fajaede' ), $atts['version'] );
		}
		if ( $date ) {
			$meta[] = $date;
		}
	}
	if ( 'yes' === $atts['count'] && $file['id'] ) {
		$count = (int) get_post_meta( $file['id'], FAJAEDE_DOWNLOAD_COUNT_KEY, true );
		/* translators: %s: aantal downloads */
		$meta[] = sprintf( _n( '%s download', '%s downloads', $count, 'fajaede' ), number_format_i18n( $count ) );
	}

	$description = null !== $content ? wpautop( do_shortcode( shortcode_unautop( trim( $content ) ) ) ) : '';

	ob_start();
	?>
	<div class="fajaede-download fajaede-download--<?php echo esc_attr( $layout ); ?>">
		<?php if ( $image ) : ?>
			<div class="fajaede-download__media"><?php echo $image; // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- gebouwd met wp_get_attachment_image / esc_url. ?></div>
		<?php endif; ?>
		<div class="fajaede-download__body">
			<?php if ( '' !== $atts['version'] && 'hero' === $layout ) : ?>
				<span class="fajaede-download__badge"><?php echo esc_html( 'v' . ltrim( $atts['version'], 'vV' ) ); ?></span>
			<?php endif; ?>
			<h3 class="fajaede-download__title"><?php echo esc_html( $title ); ?></h3>
			<?php if ( '' !== $atts['subtitle'] ) : ?>
				<p class="fajaede-download__subtitle"><?php echo esc_html( $atts['subtitle'] ); ?></p>
			<?php endif; ?>
			<?php if ( $description ) : ?>
				<div class="fajaede-download__description"><?php echo wp_kses_post( $description ); ?></div>
			<?php endif; ?>
			<?php if ( $meta ) : ?>
				<ul class="fajaede-download__meta">
					<?php foreach ( $meta as $item ) : ?>
						<li><?php echo esc_html( $item ); ?></li>
					<?php endforeach; ?>
				</ul>
			<?php endif; ?>
			<p class="fajaede-download__actions">
				<a class="wp-element-button fajaede-download__button" href="<?php echo esc_url( $file['href'] ); ?>" rel="nofollow" <?php echo $file['id'] ? '' : 'download'; ?>>
					<?php echo esc_html( $atts['button'] ); ?>
				</a>
			</p>
			<?php if ( '' !== $atts['note'] ) : ?>
				<p class="fajaede-download__note"><?php echo esc_html( $atts['note'] ); ?></p>
			<?php endif; ?>
		</div>
	</div>
	<?php
	return ob_get_clean();
}
add_shortcode( 'fajaede_download', 'fajaede_download_shortcode' );

/**
 * Raster met meerdere downloads.
 *
 * @param array       $atts    Attributen (columns).
 * @param string|null $content Geneste [fajaede_download]-shortcodes.
 * @return string
 */
function fajaede_downloads_shortcode( $atts, $content = null ) {
	$atts    = shortcode_atts( array( 'columns' => '3' ), $atts, 'fajaede_downloads' );
	$columns = max( 1, min( 4, (int) $atts['columns'] ) );

	// Verwijder de <p>/<br> die wpautop tussen geneste shortcodes zet.
	$inner = do_shortcode( shortcode_unautop( trim( (string) $content ) ) );
	$inner = preg_replace( '#^\s*(</p>|<br\s*/?>)|(<p>|<br\s*/?>)\s*$#', '', $inner );

	return sprintf(
		'<div class="fajaede-downloads" style="--fajaede-columns:%d">%s</div>',
		$columns,
		$inner
	);
}
add_shortcode( 'fajaede_downloads', 'fajaede_downloads_shortcode' );

/**
 * Zet het file-attribuut om naar bruikbare gegevens.
 *
 * @param string $file ID of URL.
 * @return array|null
 */
function fajaede_download_resolve_file( $file ) {
	$file = trim( (string) $file );
	if ( '' === $file ) {
		return null;
	}

	if ( ctype_digit( $file ) ) {
		$id = (int) $file;
		if ( 'attachment' !== get_post_type( $id ) ) {
			return null;
		}

		$path = get_attached_file( $id );
		$type = wp_check_filetype( (string) $path );

		return array(
			'id'    => $id,
			'href'  => add_query_arg( FAJAEDE_DOWNLOAD_QUERY_VAR, $id, home_url( '/' ) ),
			'title' => get_the_title( $id ),
			'type'  => $type['ext'] ? $type['ext'] : '',
			'size'  => ( $path && file_exists( $path ) ) ? filesize( $path ) : 0,
			'date'  => get_the_date( '', $id ),
		);
	}

	$url = esc_url_raw( $file );
	if ( '' === $url ) {
		return null;
	}

	$type = wp_check_filetype( (string) wp_parse_url( $url, PHP_URL_PATH ) );

	return array(
		'id'    => 0,
		'href'  => $url,
		'title' => wp_basename( (string) wp_parse_url( $url, PHP_URL_PATH ) ),
		'type'  => $type['ext'] ? $type['ext'] : '',
		'size'  => 0,
		'date'  => '',
	);
}

/**
 * Afbeelding voor een download.
 *
 * @param string $image ID of URL.
 * @param string $alt   Alt-tekst bij een URL.
 * @return string
 */
function fajaede_download_image_html( $image, $alt ) {
	$image = trim( (string) $image );
	if ( '' === $image ) {
		return '';
	}
	if ( ctype_digit( $image ) ) {
		return (string) wp_get_attachment_image( (int) $image, 'large', false, array( 'loading' => 'lazy' ) );
	}
	return sprintf( '<img src="%s" alt="%s" loading="lazy" />', esc_url( $image ), esc_attr( $alt ) );
}

/**
 * Downloadlink: teller ophogen en doorsturen naar het bestand.
 * Zo blijft de link gelijk als het bestand in de mediabibliotheek wordt vervangen.
 */
function fajaede_download_redirect() {
	// phpcs:ignore WordPress.Security.NonceVerification.Recommended -- publieke downloadlink.
	if ( ! isset( $_GET[ FAJAEDE_DOWNLOAD_QUERY_VAR ] ) ) {
		return;
	}

	// phpcs:ignore WordPress.Security.NonceVerification.Recommended
	$id = absint( $_GET[ FAJAEDE_DOWNLOAD_QUERY_VAR ] );
	if ( ! $id || 'attachment' !== get_post_type( $id ) ) {
		return; // Laat WordPress een 404 tonen.
	}

	$parent = wp_get_post_parent_id( $id );
	if ( $parent && 'publish' !== get_post_status( $parent ) && ! current_user_can( 'read_post', $parent ) ) {
		return;
	}

	$url = wp_get_attachment_url( $id );
	if ( ! $url ) {
		return;
	}

	// Bots niet meetellen.
	$agent = isset( $_SERVER['HTTP_USER_AGENT'] ) ? sanitize_text_field( wp_unslash( $_SERVER['HTTP_USER_AGENT'] ) ) : '';
	if ( ! preg_match( '/bot|crawl|spider|slurp|preview/i', $agent ) ) {
		update_post_meta( $id, FAJAEDE_DOWNLOAD_COUNT_KEY, (int) get_post_meta( $id, FAJAEDE_DOWNLOAD_COUNT_KEY, true ) + 1 );

		/**
		 * Na elke download, bv. voor analytics.
		 *
		 * @param int $id Attachment-ID.
		 */
		do_action( 'fajaede_download', $id );
	}

	nocache_headers();
	header( 'X-Robots-Tag: noindex, nofollow', true );
	wp_safe_redirect( $url, 302 );
	exit;
}
add_action( 'template_redirect', 'fajaede_download_redirect', 1 );

/**
 * Uploads (zelfde host) mogen als redirect-doel.
 *
 * @param string[] $hosts Toegestane hosts.
 * @return string[]
 */
function fajaede_download_allowed_hosts( $hosts ) {
	$uploads = wp_get_upload_dir();
	$host    = wp_parse_url( $uploads['baseurl'], PHP_URL_HOST );
	if ( $host ) {
		$hosts[] = $host;
	}
	return $hosts;
}
add_filter( 'allowed_redirect_hosts', 'fajaede_download_allowed_hosts' );

/**
 * Toon de teller in de mediabibliotheek (lijstweergave).
 *
 * @param string[] $columns Kolommen.
 * @return string[]
 */
function fajaede_download_media_column( $columns ) {
	$columns['fajaede_downloads'] = __( 'Downloads', 'fajaede' );
	return $columns;
}
add_filter( 'manage_media_columns', 'fajaede_download_media_column' );

/**
 * Inhoud van de teller-kolom.
 *
 * @param string $column Kolomnaam.
 * @param int    $id     Attachment-ID.
 */
function fajaede_download_media_column_value( $column, $id ) {
	if ( 'fajaede_downloads' === $column ) {
		echo esc_html( number_format_i18n( (int) get_post_meta( $id, FAJAEDE_DOWNLOAD_COUNT_KEY, true ) ) );
	}
}
add_action( 'manage_media_custom_column', 'fajaede_download_media_column_value', 10, 2 );
