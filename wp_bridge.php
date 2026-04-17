#!/usr/bin/env php
<?php
/**
 * WordPress Bridge Script — CLI interface for the blog pipeline.
 *
 * Bootstraps WordPress directly (no HTTP) and handles:
 *   - Creating posts with all fields (Yoast, categories, tags, etc.)
 *   - Checking for duplicate topics
 *   - Fetching site structure (pages/posts) for internal linking
 *   - Uploading featured images from URLs
 *
 * Usage:
 *   php wp_bridge.php --action=create_post --wp-path=/home/nova/public_html --json='...'
 *   php wp_bridge.php --action=check_duplicate --wp-path=/home/nova/public_html --topic="..."
 *   php wp_bridge.php --action=get_structure --wp-path=/home/nova/public_html
 *   php wp_bridge.php --action=ping --wp-path=/home/nova/public_html
 */

// Parse CLI args
$opts = getopt('', ['action:', 'wp-path:', 'json:', 'topic:', 'author:', 'image-url:', 'image-filename:']);

$action  = $opts['action'] ?? '';
$wp_path = $opts['wp-path'] ?? '';

if (!$action || !$wp_path) {
    fwrite(STDERR, "Usage: php wp_bridge.php --action=ACTION --wp-path=/path/to/wordpress [options]\n");
    exit(1);
}

// Disable WordPress trying to send emails or make HTTP requests during bootstrap
define('WP_INSTALLING', false);
define('SHORTINIT', false);

// Block all outgoing HTTP requests from WordPress during our operations
// This prevents the hang caused by WordPress trying to reach its own domain
define('WP_HTTP_BLOCK_EXTERNAL', true);

// Bootstrap WordPress
$wp_load = rtrim($wp_path, '/') . '/wp-load.php';
if (!file_exists($wp_load)) {
    echo json_encode(['error' => "wp-load.php not found at: $wp_load"]);
    exit(1);
}

// Suppress any warnings/notices during WP bootstrap
error_reporting(E_ERROR);
ob_start();
require_once($wp_load);
ob_end_clean();
error_reporting(E_ALL);

// Set current user to admin for permissions
$user = get_user_by('login', 'shehan');
if ($user) {
    wp_set_current_user($user->ID);
}

// Route to action handler
switch ($action) {
    case 'ping':
        echo json_encode([
            'status' => 'ok',
            'wp_version' => get_bloginfo('version'),
            'site_url' => get_site_url(),
            'user' => wp_get_current_user()->user_login,
        ]);
        break;

    case 'get_structure':
        handle_get_structure();
        break;

    case 'check_duplicate':
        $topic = $opts['topic'] ?? '';
        handle_check_duplicate($topic);
        break;

    case 'create_post':
        $json_str = $opts['json'] ?? '';
        $author_id = intval($opts['author'] ?? 0);
        handle_create_post($json_str, $author_id);
        break;

    case 'upload_image':
        $image_url = $opts['image-url'] ?? '';
        $filename  = $opts['image-filename'] ?? 'featured.jpg';
        handle_upload_image($image_url, $filename);
        break;

    case 'resolve_categories':
        $json_str = $opts['json'] ?? '';
        handle_resolve_categories($json_str);
        break;

    case 'resolve_tags':
        $json_str = $opts['json'] ?? '';
        handle_resolve_tags($json_str);
        break;

    default:
        echo json_encode(['error' => "Unknown action: $action"]);
        exit(1);
}

exit(0);


// ---- Action Handlers ----

function handle_get_structure() {
    $pages = get_posts([
        'post_type' => 'page',
        'post_status' => 'publish',
        'posts_per_page' => 50,
        'orderby' => 'title',
        'order' => 'ASC',
    ]);

    $posts = get_posts([
        'post_type' => 'post',
        'post_status' => ['publish', 'future'],
        'posts_per_page' => 50,
        'orderby' => 'date',
        'order' => 'DESC',
    ]);

    $links = [];
    foreach ($pages as $p) {
        $links[] = [
            'title' => html_entity_decode(get_the_title($p)),
            'url' => get_permalink($p),
            'type' => 'page',
        ];
    }
    foreach ($posts as $p) {
        $links[] = [
            'title' => html_entity_decode(get_the_title($p)),
            'url' => get_permalink($p),
            'type' => 'post',
        ];
    }

    echo json_encode(['links' => $links]);
}


function handle_check_duplicate($topic) {
    if (empty($topic)) {
        echo json_encode(['error' => 'Topic is required']);
        exit(1);
    }

    // Search for existing posts with similar titles
    $posts = get_posts([
        'post_type' => 'post',
        'post_status' => ['publish', 'future'],
        's' => $topic,
        'posts_per_page' => 5,
        'orderby' => 'relevance',
        'date_query' => [
            ['after' => date('Y-m-d', strtotime('-1 year'))],
        ],
    ]);

    // Check word overlap
    $topic_words = array_filter(
        array_map('strtolower', explode(' ', $topic)),
        function($w) { return strlen($w) >= 4; }
    );

    $is_duplicate = false;
    $match_title = '';

    if (!empty($topic_words)) {
        foreach ($posts as $post) {
            $title = html_entity_decode(get_the_title($post));
            $title_words = array_filter(
                array_map('strtolower', explode(' ', $title)),
                function($w) { return strlen($w) >= 4; }
            );
            if (empty($title_words)) continue;

            $overlap = count(array_intersect($topic_words, $title_words));
            $ratio = $overlap / count($topic_words);
            if ($ratio >= 0.6) {
                $is_duplicate = true;
                $match_title = $title;
                break;
            }
        }
    }

    echo json_encode([
        'is_duplicate' => $is_duplicate,
        'match_title' => $match_title,
        'candidates_found' => count($posts),
    ]);
}


function handle_create_post($json_str, $author_id) {
    if (empty($json_str)) {
        // Try reading from stdin
        $json_str = file_get_contents('php://stdin');
    }

    $data = json_decode($json_str, true);
    if (!$data) {
        echo json_encode(['error' => 'Invalid JSON input']);
        exit(1);
    }

    // Resolve category IDs
    $cat_ids = [];
    foreach (($data['categories'] ?? []) as $cat_name) {
        $term = get_term_by('name', $cat_name, 'category');
        if ($term) {
            $cat_ids[] = $term->term_id;
        } else {
            $new = wp_insert_term($cat_name, 'category');
            if (!is_wp_error($new)) {
                $cat_ids[] = $new['term_id'];
            }
        }
    }

    // Resolve tag IDs
    $tag_ids = [];
    foreach (($data['tags'] ?? []) as $tag_name) {
        $term = get_term_by('name', $tag_name, 'post_tag');
        if ($term) {
            $tag_ids[] = $term->term_id;
        } else {
            $new = wp_insert_term($tag_name, 'post_tag');
            if (!is_wp_error($new)) {
                $tag_ids[] = $new['term_id'];
            }
        }
    }

    // Create the post
    $post_arr = [
        'post_title'    => $data['title'] ?? 'Untitled',
        'post_content'  => $data['content_html'] ?? '',
        'post_status'   => $data['status'] ?? 'future',
        'post_date'     => $data['date'] ?? '',
        'post_date_gmt' => !empty($data['date']) ? get_gmt_from_date($data['date']) : '',
        'post_name'     => $data['slug'] ?? '',
        'post_excerpt'  => $data['excerpt'] ?? '',
        'post_author'   => $author_id ?: 1,
        'post_category' => $cat_ids,
        'tags_input'    => ($data['tags'] ?? []),
    ];

    if (!empty($data['featured_media'])) {
        // Will set after post creation
    }

    $post_id = wp_insert_post($post_arr, true);

    if (is_wp_error($post_id)) {
        echo json_encode(['error' => $post_id->get_error_message()]);
        exit(1);
    }

    // Set featured image if provided
    if (!empty($data['featured_media'])) {
        set_post_thumbnail($post_id, intval($data['featured_media']));
    }

    // Set Yoast meta
    if (!empty($data['focus_keyphrase'])) {
        update_post_meta($post_id, '_yoast_wpseo_focuskw', $data['focus_keyphrase']);
    }
    if (!empty($data['seo_title'])) {
        update_post_meta($post_id, '_yoast_wpseo_title', $data['seo_title']);
    }
    if (!empty($data['meta_description'])) {
        update_post_meta($post_id, '_yoast_wpseo_metadesc', $data['meta_description']);
    }

    echo json_encode([
        'id' => $post_id,
        'title' => get_the_title($post_id),
        'link' => get_permalink($post_id),
        'status' => get_post_status($post_id),
    ]);
}


function handle_upload_image($image_url, $filename) {
    if (empty($image_url)) {
        echo json_encode(['error' => 'image-url is required']);
        exit(1);
    }

    require_once(ABSPATH . 'wp-admin/includes/media.php');
    require_once(ABSPATH . 'wp-admin/includes/file.php');
    require_once(ABSPATH . 'wp-admin/includes/image.php');

    // Download the image
    $tmp = download_url($image_url);
    if (is_wp_error($tmp)) {
        // Fallback: use file_get_contents
        $img_data = @file_get_contents($image_url);
        if ($img_data === false) {
            echo json_encode(['error' => 'Failed to download image']);
            exit(1);
        }
        $tmp = tempnam(sys_get_temp_dir(), 'wp_img_');
        file_put_contents($tmp, $img_data);
    }

    $file_array = [
        'name' => $filename,
        'tmp_name' => $tmp,
    ];

    $media_id = media_handle_sideload($file_array, 0);
    if (is_wp_error($media_id)) {
        @unlink($tmp);
        echo json_encode(['error' => $media_id->get_error_message()]);
        exit(1);
    }

    echo json_encode(['media_id' => $media_id]);
}


function handle_resolve_categories($json_str) {
    $names = json_decode($json_str, true);
    if (!is_array($names)) {
        echo json_encode(['error' => 'Expected JSON array of category names']);
        exit(1);
    }

    $ids = [];
    foreach ($names as $name) {
        $term = get_term_by('name', $name, 'category');
        if ($term) {
            $ids[$name] = $term->term_id;
        } else {
            $new = wp_insert_term($name, 'category');
            if (!is_wp_error($new)) {
                $ids[$name] = $new['term_id'];
            }
        }
    }
    echo json_encode($ids);
}


function handle_resolve_tags($json_str) {
    $names = json_decode($json_str, true);
    if (!is_array($names)) {
        echo json_encode(['error' => 'Expected JSON array of tag names']);
        exit(1);
    }

    $ids = [];
    foreach ($names as $name) {
        $term = get_term_by('name', $name, 'post_tag');
        if ($term) {
            $ids[$name] = $term->term_id;
        } else {
            $new = wp_insert_term($name, 'post_tag');
            if (!is_wp_error($new)) {
                $ids[$name] = $new['term_id'];
            }
        }
    }
    echo json_encode($ids);
}
