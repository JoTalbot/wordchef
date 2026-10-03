package com.wordchef.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.IOException;
import java.io.InputStream;
import java.util.Locale;

/**
 * Word Chef — thin WebView shell over the static Next.js export.
 *
 * The Next.js export uses root-relative /_next and /img asset URLs.
 * When the app is loaded from file:///android_asset, Android resolves
 * those URLs outside the packaged asset tree. Intercept those local
 * asset requests and serve them from assets/www instead.
 */
public class MainActivity extends Activity {
    private WebView web;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, String url) {
                return loadPackagedAsset(url);
            }
        });

        web.setBackgroundColor(0xFFFFF6E9);
        web.loadUrl("file:///android_asset/www/index.html");
    }

    private WebResourceResponse loadPackagedAsset(String url) {
        if (url == null || !url.startsWith("file://")) {
            return null;
        }

        String path;
        try {
            path = android.net.Uri.parse(url).getPath();
        } catch (Exception ignored) {
            return null;
        }

        if (path == null) {
            return null;
        }

        if (path.startsWith("/android_asset/www/")) {
            path = path.substring("/android_asset/www/".length());
        } else if (path.startsWith("/")) {
            path = path.substring(1);
        } else {
            return null;
        }

        if (!(path.startsWith("_next/") || path.startsWith("img/") || path.equals("favicon.ico"))) {
            return null;
        }

        if (path.contains("..")) {
            return null;
        }

        try {
            InputStream stream = getAssets().open("www/" + path);
            String mime = mimeType(path);
            String encoding = isTextAsset(path) ? "UTF-8" : null;
            return new WebResourceResponse(mime, encoding, stream);
        } catch (IOException ignored) {
            return null;
        }
    }

    private static boolean isTextAsset(String path) {
        String lower = path.toLowerCase(Locale.ROOT);
        return lower.endsWith(".css")
                || lower.endsWith(".js")
                || lower.endsWith(".mjs")
                || lower.endsWith(".json")
                || lower.endsWith(".html")
                || lower.endsWith(".txt")
                || lower.endsWith(".svg");
    }

    private static String mimeType(String path) {
        String lower = path.toLowerCase(Locale.ROOT);
        if (lower.endsWith(".css")) return "text/css";
        if (lower.endsWith(".js") || lower.endsWith(".mjs")) return "application/javascript";
        if (lower.endsWith(".json")) return "application/json";
        if (lower.endsWith(".html")) return "text/html";
        if (lower.endsWith(".svg")) return "image/svg+xml";
        if (lower.endsWith(".webp")) return "image/webp";
        if (lower.endsWith(".png")) return "image/png";
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
        if (lower.endsWith(".gif")) return "image/gif";
        if (lower.endsWith(".ico")) return "image/x-icon";
        if (lower.endsWith(".woff2")) return "font/woff2";
        if (lower.endsWith(".woff")) return "font/woff";
        if (lower.endsWith(".ttf")) return "font/ttf";
        return "application/octet-stream";
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
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
