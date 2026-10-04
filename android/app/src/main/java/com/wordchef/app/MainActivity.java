package com.wordchef.app;

import android.app.Activity;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.IOException;
import java.io.InputStream;
import java.net.URLConnection;

import android.net.Uri;

/**
 * Word Chef WebView shell over the packaged Next.js export.
 *
 * The frontend is packaged under APK assets/www. Android's built-in
 * AssetsPathHandler maps to the APK asset root, not a subdirectory, so
 * requests are served explicitly from assets/www while keeping the
 * appassets.androidplatform.net HTTPS origin used by WebView.
 */
public class MainActivity extends Activity {
    private static final String ASSET_HOST = "appassets.androidplatform.net";
    private static final String ASSET_ROOT = "www/";

    private WebView web;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        web = new WebView(this);
        setContentView(web);

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(
                    WebView view,
                    WebResourceRequest request
            ) {
                return servePackagedAsset(request.getUrl());
            }

            @Override
            @SuppressWarnings("deprecation")
            public WebResourceResponse shouldInterceptRequest(
                    WebView view,
                    String url
            ) {
                return servePackagedAsset(Uri.parse(url));
            }
        });

        web.setBackgroundColor(0xFFFFF6E9);
        web.loadUrl("https://" + ASSET_HOST + "/assets/index.html");
    }

    /**
     * Map the virtual HTTPS paths used by the WebView to APK assets/www.
     *
     * /assets/foo        -> assets/www/foo
     * /_next/foo         -> assets/www/_next/foo
     * /img/foo           -> assets/www/img/foo
     */
    private WebResourceResponse servePackagedAsset(Uri uri) {
        if (uri == null || !ASSET_HOST.equals(uri.getHost())) {
            return null;
        }

        String path = uri.getPath();
        if (path == null || path.contains("..")) {
            return null;
        }

        String relative;
        if (path.startsWith("/assets/")) {
            relative = path.substring("/assets/".length());
        } else if (path.startsWith("/_next/")) {
            relative = "_next/" + path.substring("/_next/".length());
        } else if (path.startsWith("/img/")) {
            relative = "img/" + path.substring("/img/".length());
        } else {
            return null;
        }

        if (relative.isEmpty() || relative.startsWith("/") || relative.contains("..")) {
            return null;
        }

        String assetPath = ASSET_ROOT + relative;

        try {
            InputStream stream = getAssets().open(assetPath);
            String mime = URLConnection.guessContentTypeFromName(assetPath);
            if (mime == null) {
                if (assetPath.endsWith(".js")) mime = "application/javascript";
                else if (assetPath.endsWith(".css")) mime = "text/css";
                else if (assetPath.endsWith(".html")) mime = "text/html";
                else if (assetPath.endsWith(".json")) mime = "application/json";
                else mime = "application/octet-stream";
            }

            String encoding = isTextAsset(mime) ? "UTF-8" : null;
            return new WebResourceResponse(mime, encoding, stream);
        } catch (IOException ignored) {
            return null;
        }
    }

    private boolean isTextAsset(String mime) {
        return mime.startsWith("text/")
                || mime.contains("javascript")
                || mime.contains("json")
                || mime.contains("xml");
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.destroy();
        }
        super.onDestroy();
    }
}
