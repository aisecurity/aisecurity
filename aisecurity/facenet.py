"""

"aisecurity.facenet"

Facial recognition with FaceNet in Keras.

Paper: https://arxiv.org/pdf/1503.03832.pdf

"""

import asyncio
import warnings

import keras
from keras import backend as K
import matplotlib.pyplot as plt
from sklearn import neighbors
from termcolor import cprint

from aisecurity.logging import log
from aisecurity.utils.paths import CONFIG_HOME
from aisecurity.utils.preprocessing import *
from aisecurity.utils.dataflow import *


# FACENET
class FaceNet(object):


    # HYPERPARAMETERS
    HYPERPARAMS = {
        "alpha": 0.8,
        "mtcnn_alpha": 0.99
    }


    # INITS
    @timer(message="Model load time")
    def __init__(self, filepath=CONFIG_HOME + "/models/ms_celeb_1m.h5"):
        assert os.path.exists(filepath), "{} not found".format(filepath)
        self.facenet = keras.models.load_model(filepath)

        self.__static_db = None  # must be filled in by user
        self.__dynamic_db = {}  # used for real-time database updating (i.e., for visitors)

        CONSTANTS["img_size"] = (self.facenet.input_shape[1], self.facenet.input_shape[1])


    # MUTATORS
    def set_data(self, data):
        assert data is not None, "data must be provided"

        def check_validity(data):
            for key in data.keys():
                assert isinstance(key, str), "data keys must be person names"
                data[key] = np.asarray(data[key])
                is_vector = data[key].ndim <= 2 and (1 in data[key].shape or data[key].ndim == 1)
                assert is_vector, "each data[key] must be a vectorized embedding"
            return data

        self.__static_db = check_validity(data)

        try:
            self._train_knn(knn_types=["static"])
            self.dynamic_knn = None
        except ValueError:
            raise ValueError("Current model incompatible with database")

    def _train_knn(self, knn_types):
        def knn_factory(data):
            names, embeddings = zip(*data.items())
            knn = neighbors.KNeighborsClassifier(n_neighbors=len(names) // len(set(names)))
            knn.fit(embeddings, names)
            return knn

        if self.__static_db and "static" in knn_types:
            self.static_knn = knn_factory(self.__static_db)
        if self.__dynamic_db and "dynamic" in knn_types:
            self.dynamic_knn = knn_factory(self.__dynamic_db)


    # RETRIEVERS
    @property
    def data(self):
        return self.__static_db

    def get_embeds(self, data, *args, **kwargs):
        embeds = []
        for n in args:
            if isinstance(n, str):
                try:
                    n = data[n]
                except KeyError:
                    n = self.predict([n], margin=CONSTANTS["margin"], **kwargs)
            elif not (n.ndim <= 2 and (1 in n.shape or n.ndim == 1)):  # n must be a vector
                n = self.predict([n], margin=CONSTANTS["margin"], **kwargs)
            embeds.append(n)
        return embeds if len(embeds) > 1 else embeds[0]

    def predict(self, paths_or_imgs, margin=CONSTANTS["margin"], faces=None):
        l2_normalize = lambda x: x / np.sqrt(np.maximum(np.sum(np.square(x), axis=-1, keepdims=True), K.epsilon()))

        aligned_imgs = whiten(align_imgs(paths_or_imgs, margin, faces=faces))
        raw_embeddings = self.facenet.predict(aligned_imgs)
        normalized_embeddings = l2_normalize(raw_embeddings)

        return normalized_embeddings


    # FACIAL RECOGNITION HELPER
    @timer(message="Recognition time")
    def _recognize(self, img, faces=None, db_types=None):
        assert self.__static_db or self.__dynamic_db, "data must be provided"

        knns, data = [], {}
        if db_types is None or "static" in db_types:
            knns.append(self.static_knn)
            data.update(self.__static_db)
        if "dynamic" in db_types and self.dynamic_knn and self.__dynamic_db:
            knns.append(self.dynamic_knn)
            data.update(self.__dynamic_db)

        embedding = self.get_embeds(data, img, faces=faces)
        best_matches = []
        for knn in reversed(knns):
            pred = knn.predict(embedding)[0]
            best_matches.append((pred, np.linalg.norm(embedding - data[pred])))
        best_match, l2_dist = sorted(best_matches, key=lambda n: n[1])[0]
        is_recognized = l2_dist <= FaceNet.HYPERPARAMS["alpha"]

        return embedding, is_recognized, best_match, l2_dist

    # FACIAL RECOGNITION
    def recognize(self, img, verbose=True):
        # img can be a path, image, database name, or embedding
        _, is_recognized, best_match, l2_dist = self._recognize(img)

        if verbose:
            if is_recognized:
                print("Your image is a picture of \"{}\": L2 distance of {}".format(best_match, l2_dist))
            else:
                print("Your image is not in the database. The best match is \"{}\" with an L2 distance of ".format(
                    best_match, l2_dist))

        return is_recognized, best_match, l2_dist


    # REAL-TIME FACIAL RECOGNITION HELPER
    async def _real_time_recognize(self, width, height, use_log, use_dynamic, use_picam, use_graphics, framerate,
                                   resize):
        db_types = ["static"]
        if use_dynamic:
            db_types.append("dynamic")
        if use_log:
            log.init(flush=True)

        cap = self.get_video_cap(width, height, picamera=use_picam, framerate=framerate)

        if resize:
            width, height = width * resize, height * resize

        mtcnn = MTCNN(min_face_size=0.5 * (width + height) / 3)  # face needs to fill at least 1/3 of the frame

        missed_frames = 0
        l2_dists = []

        start = time.time()

        while True:
            _, frame = cap.read()
            original_frame = frame.copy()
            if resize:
                frame = cv2.resize(frame, (0, 0), fx=resize, fy=resize)

            # using MTCNN to detect faces
            result = mtcnn.detect_faces(frame)

            if result:
                overlay = original_frame.copy()

                for person in result:
                    face = person["box"]

                    if person["confidence"] < self.HYPERPARAMS["mtcnn_alpha"]:
                        print("Face poorly detected")
                        continue

                    # facial recognition
                    try:
                        embedding, is_recognized, best_match, l2_dist = self._recognize(frame, face, db_types)
                        print(
                            "L2 distance: {} ({}){}".format(l2_dist, best_match, " !" if not is_recognized else ""))
                    except (
                    ValueError, cv2.error) as error:  # error-handling using names is unstable-- change later
                        if "query data dimension" in str(error):
                            raise ValueError("Current model incompatible with database")
                        elif "empty" in str(error):
                            print("Image refresh rate too high")
                        elif "opencv" in str(error):
                            print("Failed to capture frame")
                        else:
                            raise error
                        continue

                    # add graphics
                    if use_graphics:
                        self.add_graphics(original_frame, overlay, person, width, height, is_recognized, best_match,
                                          resize)

                    if time.time() - start > 5.:  # wait 5 seconds before logging starts

                        # update dynamic database
                        if use_dynamic:
                            self.dynamic_update(embedding, l2_dists)

                        # log activity
                        if use_log:
                            self.log_activity(is_recognized, best_match, original_frame, log_unknown=True)

                        l2_dists.append(l2_dist)

            else:
                missed_frames += 1
                if missed_frames > log.THRESHOLDS["missed_frames"]:
                    missed_frames = 0
                    log.flush_current()
                    l2_dists = []
                print("No face detected")

            cv2.imshow("AI Security v1.0a", original_frame)

            await asyncio.sleep(1e-6)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        cap.release()
        cv2.destroyAllWindows()

    # REAL-TIME FACIAL RECOGNITION
    def real_time_recognize(self, width=640, height=360, use_log=True, use_dynamic=False, use_picam=False,
                            framerate=20, use_graphics=True, resize=None):
        async def async_helper(recognize_func, *args, **kwargs):
            await recognize_func(*args, **kwargs)

        loop = asyncio.new_event_loop()
        task = loop.create_task(async_helper(self._real_time_recognize, width, height, use_log,
                                             use_dynamic=use_dynamic, use_graphics=use_graphics,
                                             use_picam=use_picam, framerate=framerate, resize=resize))
        loop.run_until_complete(task)


    # GRAPHICS
    @staticmethod
    def get_video_cap(width, height, picamera, framerate):
        def _gstreamer_pipeline(capture_width=1280, capture_height=720, display_width=640, display_height=360,
                                framerate=20, flip_method=0):
            return (
                    "nvarguscamerasrc ! video/x-raw(memory:NVMM), width=(int)%d, height=(int)%d, format=(string)NV12,"
                    " framerate=(fraction)%d/1 ! nvvidconv flip-method=%d ! video/x-raw, width=(int)%d, height=(int)%d,"
                    " format=(string)BGRx ! videoconvert ! video/x-raw, format=(string)BGR ! appsink"
                    % (capture_width, capture_height, framerate, flip_method, display_width, display_height)
            )

        if picamera:
            return cv2.VideoCapture(
                _gstreamer_pipeline(display_width=width, display_height=height, framerate=framerate),
                cv2.CAP_GSTREAMER)
        else:
            cap = cv2.VideoCapture(0)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            return cap

    @staticmethod
    def add_graphics(frame, overlay, person, width, height, is_recognized, best_match, resize):
        line_thickness = round(1e-6 * width * height + 1.5)
        radius = round((1e-6 * width * height + 1.5) / 2.)
        font_size = 4.5e-7 * width * height + 0.5
        # works for 6.25e4 pixel video cature to 1e6 pixel video capture

        def get_color(is_recognized, best_match):
            if not is_recognized:
                return 0, 0, 255  # red
            elif "visitor" in best_match:
                return 218, 112, 214  # purple (actually more of an "orchid")
            else:
                return 0, 255, 0  # green

        def add_box_and_label(frame, origin, corner, color, line_thickness, best_match, font_size, thickness):
            cv2.rectangle(frame, origin, corner, color, line_thickness)
            # label box
            cv2.rectangle(frame, (origin[0], corner[1] - 35), corner, color, cv2.FILLED)
            cv2.putText(frame, best_match.replace("_", " ").title(), (origin[0] + 6, corner[1] - 6),
                        cv2.FONT_HERSHEY_DUPLEX, font_size, (255, 255, 255), thickness)  # white text

        def add_features(overlay, features, radius, color, line_thickness):
            cv2.circle(overlay, (features["left_eye"]), radius, color, line_thickness)
            cv2.circle(overlay, (features["right_eye"]), radius, color, line_thickness)
            cv2.circle(overlay, (features["nose"]), radius, color, line_thickness)
            cv2.circle(overlay, (features["mouth_left"]), radius, color, line_thickness)
            cv2.circle(overlay, (features["mouth_right"]), radius, color, line_thickness)

            cv2.line(overlay, features["left_eye"], features["nose"], color, radius)
            cv2.line(overlay, features["right_eye"], features["nose"], color, radius)
            cv2.line(overlay, features["mouth_left"], features["nose"], color, radius)
            cv2.line(overlay, features["mouth_right"], features["nose"], color, radius)

        features = person["keypoints"]
        x, y, height, width = person["box"]

        if resize:
            scale_factor = 1. / resize

            scale = lambda x: tuple(int(element * scale_factor) for element in x)
            features = {feature: scale(features[feature]) for feature in features}

            scale = lambda *xs: tuple(int(x * scale_factor) for x in xs)
            x, y, height, width = scale(x, y, height, width)

        color = get_color(is_recognized, best_match)

        margin = CONSTANTS["margin"]
        origin = (x - margin // 2, y - margin // 2)
        corner = (x + height + margin // 2, y + width + margin // 2)

        add_features(overlay, features, radius, color, line_thickness)
        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

        text = best_match if is_recognized else ""
        add_box_and_label(frame, origin, corner, color, line_thickness, text, font_size, thickness=1)


    # DISPLAY
    def show_embeds(self, encrypted=False, single=False):
        assert self.data, "data must be provided to show embeddings"

        def closest_multiples(n):
            if n == 0 or n == 1:
                return n, n
            factors = [((i, int(n / i)), (abs(i - int(n / i)))) for i in range(1, n) if n % i == 0]
            return sorted(factors, key=lambda n: n[1])[0][0]

        if encrypted:
            data = DataEncryption.encrypt_data(self.data, ignore=["embeddings"], decryptable=False)
        else:
            data = self.data

        for person in data:
            embed = np.asarray(data[person])
            embed = embed.reshape(*closest_multiples(embed.shape[0]))

            plt.imshow(embed, cmap="gray")
            try:
                plt.title(person)
            except TypeError:
                warnings.warn("encrypted data cannot be displayed due to presence of non-UTF8-decodable values")
            plt.axis("off")
            plt.show()

            if single and person == list(data.keys())[0]:
                break


    # LOGGING
    @staticmethod
    def log_activity(is_recognized, best_match, frame, log_unknown=True):

        cooldown_ok = lambda t: time.time() - t > log.THRESHOLDS["cooldown"]

        def get_mode(d):
            max_key = list(d.keys())[0]
            for key in d:
                if len(d[key]) > len(d[max_key]):
                    max_key = key
            return max_key

        log.update_current_logs(is_recognized, best_match)

        if log.num_recognized >= log.THRESHOLDS["num_recognized"] and cooldown_ok(log.last_logged):
            if log.get_percent_diff(best_match) <= log.THRESHOLDS["percent_diff"]:
                recognized_person = get_mode(log.current_log)
                log.log_person(recognized_person, times=log.current_log[recognized_person])
                cprint("Regular activity logged", color="green", attrs=["bold"])

        if log_unknown and log.num_unknown >= log.THRESHOLDS["num_unknown"] and cooldown_ok(log.unk_last_logged):
            path = CONFIG_HOME + "/database/unknown/{}.jpg".format(
                len(os.listdir(CONFIG_HOME + "/database/unknown")))
            log.log_unknown(path)

            # recording unknown images is deprecated and will be removed/changed later
            cv2.imwrite(path, frame)
            cprint("Unknown activity logged", color="red", attrs=["bold"])


    # DYNAMIC DATABASE
    def dynamic_update(self, embedding, l2_dists):
        previous_frames = l2_dists[-log.THRESHOLDS["num_unknown"]:]
        filtered = list(filter(lambda x: x > self.HYPERPARAMS["alpha"], previous_frames))

        if len(l2_dists) >= log.THRESHOLDS["num_unknown"] and len(filtered) > 0:
            mostly_unknown = len(filtered) / len(previous_frames) >= 1. - log.THRESHOLDS["percent_diff"]

            if mostly_unknown and np.std(filtered) <= log.THRESHOLDS["percent_diff"] / 2.:
                self.__dynamic_db["visitor_{}".format(len(self.__dynamic_db) + 1)] = embedding.flatten()
                self._train_knn(knn_types=["dynamic"])
                log.flush_current()
