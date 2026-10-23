import android.content.Context;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Looper;
import org.json.JSONArray;
import org.json.JSONObject;

/** Media metadata through the existing ADB shell permission; "next" also moves Spotify TV to its next track. */
public final class MediaProbe {
    public static void main(String[] args) {
        try {
            Looper.prepareMainLooper();
            Class<?> activityThread = Class.forName("android.app.ActivityThread");
            Object thread = activityThread.getMethod("systemMain").invoke(null);
            Context system = (Context) activityThread.getMethod("getSystemContext").invoke(thread);
            Context context = system.createPackageContext("com.android.shell", 0);
            if (android.os.Build.VERSION.SDK_INT >= 31) {
                // app_process does not run the normal application's media initialization.
                for (String name : new String[]{"MediaFrameworkPlatformInitializer", "MediaFrameworkInitializer"}) {
                    Class<?> init = Class.forName("android.media." + name);
                    if (init.getMethod("getMediaServiceManager").invoke(null) == null) {
                        Class<?> service = Class.forName("android.media.MediaServiceManager");
                        init.getMethod("setMediaServiceManager", service).invoke(null, service.getConstructor().newInstance());
                    }
                }
            }
            MediaSessionManager manager = (MediaSessionManager) context.getSystemService(Context.MEDIA_SESSION_SERVICE);
            boolean next = args.length > 0 && args[0].equals("next"), spotify = false;
            JSONArray result = new JSONArray();
            for (MediaController controller : manager.getActiveSessions(null)) {
                String pkg = controller.getPackageName();
                if (!pkg.equals("com.spotify.tv.android") && !pkg.equals("org.smarttube.stable")) continue;
                JSONObject item = new JSONObject().put("package", pkg);
                PlaybackState state = controller.getPlaybackState();
                if (next && pkg.equals("com.spotify.tv.android")) {
                    spotify = true;
                    // Already playing means someone picked a track: never skip it.
                    if (state == null || state.getState() != PlaybackState.STATE_PLAYING) {
                        controller.getTransportControls().skipToNext();
                        controller.getTransportControls().play();
                    }
                }
                item.put("state", state == null ? -1 : state.getState());
                item.put("position_ms", state == null ? -1 : state.getPosition());
                MediaMetadata meta = controller.getMetadata();
                if (meta != null) {
                    item.put("id", meta.getString(MediaMetadata.METADATA_KEY_MEDIA_ID));
                    item.put("title", meta.getString(MediaMetadata.METADATA_KEY_TITLE));
                    item.put("artist", meta.getString(MediaMetadata.METADATA_KEY_ARTIST));
                    item.put("album", meta.getString(MediaMetadata.METADATA_KEY_ALBUM));
                    item.put("display_subtitle", meta.getString(MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE));
                    item.put("album_artist", meta.getString(MediaMetadata.METADATA_KEY_ALBUM_ARTIST));
                    item.put("duration_ms", meta.getLong(MediaMetadata.METADATA_KEY_DURATION));
                    item.put("media_uri", meta.getString(MediaMetadata.METADATA_KEY_MEDIA_URI));
                    item.put("advertisement", meta.getLong("android.media.metadata.ADVERTISEMENT") != 0);
                }
                result.put(item);
            }
            if (next && !spotify) {
                System.err.println("No Spotify TV media session");
                System.exit(1);
            }
            System.out.println(result.toString());
            System.exit(0);
        } catch (Throwable error) {
            error.printStackTrace(System.err);
            System.exit(1);
        }
    }
}
