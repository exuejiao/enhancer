import numpy as np
import tensorflow as tf

from utils import parse_function


def get_input_fn(filenames, num_epochs=None, shuffle=False, batch_size=1):
    return lambda: input_fn(filenames, num_epochs, shuffle, batch_size)


def input_fn(filenames, epoch, shuffle, batch_size):
    dataset = tf.contrib.data.TFRecordDataset(filenames)
    dataset = dataset.map(parse_function)
    dataset = dataset.repeat(epoch)
    if shuffle:
        dataset = dataset.shuffle(buffer_size=10000)
    dataset = dataset.batch(batch_size)
    iterator = dataset.make_one_shot_iterator()
    features, labels, names = iterator.get_next()
    print(names)
    return features, labels


def srcnn_model_fn(features, labels, mode, params):
    learning_rate = params.learning_rate
    filter_shapes = [1, 2, 1]
    channels = 1
    device = '/device:%s:0' % params.device
    with tf.device(device):
        with tf.name_scope('inputs'):
            lr_images = features
            hr_images = labels

        with tf.name_scope('weights'):
            w1 = tf.Variable(tf.random_normal([filter_shapes[0], filter_shapes[0], channels, 64], stddev=1e-3), name='cnn_w1')
            w2 = tf.Variable(tf.random_normal([filter_shapes[1], filter_shapes[1], 64, 32], stddev=1e-3), name='cnn_w2')
            w3 = tf.Variable(tf.random_normal([filter_shapes[2], filter_shapes[2], 32, channels], stddev=1e-3), name='cnn_w3')

        with tf.name_scope('biases'):
            b1 = tf.Variable(tf.zeros([64]), name='cnn_b1')
            b2 = tf.Variable(tf.zeros([32]), name='cnn_b2')
            b3 = tf.Variable(tf.zeros([channels]), name='cnn_b3')

        with tf.name_scope('prediction'):
            conv1 = tf.nn.bias_add(tf.nn.conv2d(lr_images, w1, strides=[1, 1, 1, 1], padding='SAME'), b1, name='conv_1')
            conv1r = tf.nn.relu(conv1, name='relu_1')
            conv2 = tf.nn.bias_add(tf.nn.conv2d(conv1r, w2, strides=[1, 1, 1, 1], padding='SAME'), b2, name='conv_2')
            conv2r = tf.nn.relu(conv2, name='relu_2')
            conv3 = tf.nn.bias_add(tf.nn.conv2d(conv2r, w3, strides=[1, 1, 1, 1], padding='SAME'), b3, name='conv_3')
            prediction = conv3

        with tf.name_scope('losses'):
            mse = tf.losses.mean_squared_error(hr_images, prediction)
            rmse = tf.sqrt(mse)
            log_loss = tf.losses.log_loss(hr_images, prediction)
            huber_loss = tf.losses.huber_loss(hr_images, prediction)
            psnr = compute_psnr(mse)
            ssim = compute_ssim(hr_images, prediction)
            eval_metric_ops = {
                "rmse": tf.metrics.root_mean_squared_error(features, prediction)
            }

        with tf.name_scope('train'):
            train_op = tf.train.AdamOptimizer(learning_rate).minimize(mse, tf.train.get_global_step())

    tf.summary.scalar('mse', mse)
    tf.summary.scalar('rmse', rmse)
    tf.summary.scalar('psnr', psnr)
    tf.summary.scalar('ssim', ssim)
    tf.summary.scalar('log_loss', log_loss)
    tf.summary.scalar('huber_loss', huber_loss)

    # tf.summary.image('prediction', prediction)
    # summary_op = tf.summary.merge_all()

    logging_params = {'mse': mse, 'rmse': rmse, 'ssim': ssim, 'psnr': psnr, 'log_loss': log_loss, 'huber_loss': huber_loss, 'step': tf.train.get_global_step()}
    logging_hook = tf.train.LoggingTensorHook(logging_params, every_n_iter=1)

    return tf.estimator.EstimatorSpec(
        mode=mode,
        loss=mse,
        predictions=prediction,
        train_op=train_op,
        training_hooks=[logging_hook],
        eval_metric_ops=eval_metric_ops
    )


def get_estimator(run_config=None, params=None):
    """Return the model as a Tensorflow Estimator object.
    Args:
         run_config (RunConfig): Configuration for Estimator run.
         params (HParams): hyperparameters.
    """
    return tf.estimator.Estimator(
        model_fn=srcnn_model_fn,  # First-class function
        params=params,  # HParams
        config=run_config  # RunConfig
    )


def _tf_fspecial_gauss(size, sigma):
    """Function to mimic the 'fspecial' gaussian MATLAB function
    :param size:
    :param sigma:
    :return:
    """
    x_data, y_data = np.mgrid[-size // 2 + 1:size // 2 + 1, -size // 2 + 1:size // 2 + 1]

    x_data = np.expand_dims(x_data, axis=-1)
    x_data = np.expand_dims(x_data, axis=-1)

    y_data = np.expand_dims(y_data, axis=-1)
    y_data = np.expand_dims(y_data, axis=-1)

    x = tf.constant(x_data, dtype=tf.float32)
    y = tf.constant(y_data, dtype=tf.float32)

    g = tf.exp(-((x ** 2 + y ** 2) / (2.0 * sigma ** 2)))
    return g / tf.reduce_sum(g)


def compute_ssim(img1, img2, cs_map=False, mean_metric=True, size=11, sigma=1.5):
    window = _tf_fspecial_gauss(size, sigma)  # window shape [size, size]
    K1 = 0.01
    K2 = 0.03
    L = 1  # depth of image (255 in case the image has a differnt scale)
    C1 = (K1 * L) ** 2
    C2 = (K2 * L) ** 2
    mu1 = tf.nn.conv2d(img1, window, strides=[1, 1, 1, 1], padding='VALID')
    mu2 = tf.nn.conv2d(img2, window, strides=[1, 1, 1, 1], padding='VALID')
    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = tf.nn.conv2d(img1 * img1, window, strides=[1, 1, 1, 1], padding='VALID') - mu1_sq
    sigma2_sq = tf.nn.conv2d(img2 * img2, window, strides=[1, 1, 1, 1], padding='VALID') - mu2_sq
    sigma12 = tf.nn.conv2d(img1 * img2, window, strides=[1, 1, 1, 1], padding='VALID') - mu1_mu2
    if cs_map:
        value = (((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / ((mu1_sq + mu2_sq + C1) *
                                                              (sigma1_sq + sigma2_sq + C2)),
                 (2.0 * sigma12 + C2) / (sigma1_sq + sigma2_sq + C2))
    else:
        value = ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / ((mu1_sq + mu2_sq + C1) *
                                                             (sigma1_sq + sigma2_sq + C2))

    if mean_metric:
        value = tf.reduce_mean(value)
    return value


def compute_psnr(mse):
    """
    PSNR is Peek Signal to Noise Ratio, which is similar to mean squared error.

    It can be calculated as
    PSNR = 20 * log10(MAXp) - 10 * log10(MSE)

    When providing an unscaled input, MAXp = 255. Therefore 20 * log10(255)== 48.1308036087.
    However, since we are scaling our input, MAXp = 1. Therefore 20 * log10(1) = 0.
    Thus we remove that component completely and only compute the remaining MSE component.

    Modify from https://github.com/titu1994/Image-Super-Resolution
    """
    return -10. * tf.log(mse) / tf.log(10.)
