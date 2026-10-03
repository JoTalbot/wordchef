package com.wordchef.app;

import android.app.Activity;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.MimeTypeMap;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.webkit.WebViewAssetLoader;

import java.io.IOException;
import java.io.InputStream;

/**
 * Word Chef WebView shell over the packaged Next.js export.
 *
 * The loader exposes assets/www as the root of a local HTTPS origin.
 * This makes Next.js root-relative /_next and /img URLs resolve normally.
 */
public class MainActivity extends Activity {
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

        WebViewAssetLoader assetLoader = new WebViewAssetLoader.Builder()
                .setDomain("wordchef.local")
                .addPathHandler("/", new WordChefPathHandler())
                .build();

        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, String url) {
                return assetLoader.shouldInterceptRequest(android.net.Uri.parse(url));
            }
        });

        web.setBackgroundColor(0xFFFFF6E9);
        web.loadUrl("https://wordchef.local/index.html");
    }

    private final class WordChefPathHandler implements WebViewAssetLoader.PathHandler {
        @Override
        public WebResourceResponse handle(String path) {
            if (path == null || path.isEmpty() || path.contains("..")) {
                return null;
            }

            String assetPath = path.startsWith("/") ? path.substring(1) : path;
            if (assetPath.isEmpty()) {
                assetPath = "index.html";
            }

            try {
                InputStream stream = getAssets().open("www/" + assetPath);
                String mime = MimeTypeMap.getSingleton()
                        .getMimeTypeFromExtension(MimeTypeMap.getFileExtensionFromUrl(assetPath));
                if (mime == null) {
                    mime = "application/octet-stream";
                }

                String encoding = isText(assetPath) ? "UTF-8" : null;
                return new WebResourceResponse(mime, encoding, stream);
            } catch (IOException ignored) {
                return null;
            }
        }

        private boolean isText(String path) {
            String lower = path.toLowerCase(java.util.Locale.ROOT);
            return lower.endsWith(".html")
                    || lower.endsWith(".css")
                    || lower.endsWith(".js")
                    || lower.endsWith(".mjs")
                    || lower.endsWith(".json")
                    || lower.endsWith(".svg")
                    || lower.endsWith(".txt");
        }
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
