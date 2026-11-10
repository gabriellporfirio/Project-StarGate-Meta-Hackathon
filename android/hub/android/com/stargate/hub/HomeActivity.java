package com.stargate.hub;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * The phone's home screen: one full-screen view of the phone's own
 * page (settings and controls only). It can only ever show that page;
 * every other address is refused.
 */
public final class HomeActivity extends Activity {
    private static final String ORIGIN =
            "http://127.0.0.1:" + HubServer.DEFAULT_PORT;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private WebView web;
    private boolean loaded;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setStatusBarColor(Color.rgb(6, 8, 15));
        getWindow().setNavigationBarColor(Color.rgb(6, 8, 15));
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(6, 8, 15));
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setGeolocationEnabled(false);
        settings.setSaveFormData(false);
        settings.setSupportZoom(false);
        settings.setTextZoom(100);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view,
                    WebResourceRequest request) {
                return !request.getUrl().toString().startsWith(ORIGIN + "/");
            }

            @Override
            public void onReceivedError(WebView view, int code,
                    String description, String url) {
                loaded = false;
                handler.postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        open();
                    }
                }, 1500);
            }
        });
        setContentView(web);
        HubService.begin(this);
        open();
    }

    private void open() {
        if (loaded) {
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String token =
                        HubService.screenToken(getApplicationContext());
                handler.post(new Runnable() {
                    @Override
                    public void run() {
                        if (token == null) {
                            handler.postDelayed(new Runnable() {
                                @Override
                                public void run() {
                                    open();
                                }
                            }, 1500);
                            return;
                        }
                        CookieManager.getInstance().setCookie(ORIGIN,
                                "screen=" + token + "; Path=/; HttpOnly; "
                                        + "SameSite=Strict");
                        loaded = true;
                        web.loadUrl(ORIGIN + "/phone");
                    }
                });
            }
        }, "hub-open").start();
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        open();
    }

    @Override
    protected void onPause() {
        web.onPause();
        super.onPause();
    }

    @Override
    public void onBackPressed() {
        // The home screen has nowhere to go back to.
    }
}
