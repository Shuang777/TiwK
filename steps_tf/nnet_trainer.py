import shutil
import os
import time
import numpy as np
import tensorflow as tf
import nnet
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class Nnet(object):
    '''a class for a neural network that can be used together with Kaldi'''

    def __init__(self, conf, op_conf, input_dim, output_dim, seed=777):

        #get nnet structure configs
        self.conf = dict(conf)
        self.op_conf = dict(op_conf)

        self.input_dim = input_dim
        self.output_dim = output_dim
        self.hidden_units = int(self.conf['hidden_units'])
        self.hidden_layers = int(self.conf['num_hidden_layers'])
        self.batch_size = int(self.conf['batch_size'])

        self.graph = tf.Graph()
        with self.graph.as_default():

            tf.set_random_seed(seed)

            self.feats_holder, self.labels_holder = nnet.placeholder_inputs(self.input_dim, self.batch_size)
            self.learning_rate_holder = tf.placeholder(tf.float32, shape=[])

            if self.conf.get('init_file', None) is not None:
                logits = nnet.inference_from_file(self.feats_holder, 
                        self.input_dim, self.output_dim, self.conf['init_file'])
            else:
                logits = nnet.inference(self.feats_holder, self.input_dim, 
                        self.hidden_units, self.hidden_layers, 
                        self.output_dim, self.conf['nonlin'],
                        self.conf['init'], self.conf.get('batch_norm', False))

            self.outputs = tf.nn.softmax(logits)

            self.loss = nnet.loss(logits, self.labels_holder)

            self.train_op = nnet.training(self.op_conf, self.loss, self.learning_rate_holder)

            self.init = tf.global_variables_initializer()

            self.eval_acc = nnet.evaluation(logits, self.labels_holder)

            save_list = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES)

            self.saver = tf.train.Saver(save_list, max_to_keep=20)

        self.sess = tf.Session(graph=self.graph)


    def test(self, logfile, data_gen):

        fh = logging.FileHandler(logfile, mode = 'w')
        logger.addHandler(fh)

        sum_avg_loss = 0
        sum_frames = 0
        count_steps = 0

        sum_accs = 0
        sum_acc_frames = 0

        start_time = time.time()

        while(True):

            feed_dict = data_gen.get_batch(self.feats_holder, self.labels_holder)

            if feed_dict is None:
                break

            loss = self.sess.run(self.loss, feed_dict = feed_dict)

            sum_avg_loss += loss
            sum_frames += data_gen.get_batch_size()
            count_steps += 1

            if count_steps % 1000 == 0 or count_steps == 1:
                acc = self.sess.run(self.eval_acc, feed_dict = feed_dict)
                sum_accs += acc
                sum_acc_frames += data_gen.get_batch_size()
                logger.info("%s frames processed", sum_frames)

        duration = time.time() - start_time

        if count_steps == 0:
            raise RuntimeError('nnet_trainer.test(): count_steps = 0')

        data_gen.reset_batch()
        avg_loss = sum_avg_loss / count_steps
        logger.info("Test: avg loss = %.6f on %d frames (%.2f sec passed, %.2f frames per sec), peek acc: %.2f%%", avg_loss, sum_frames, duration, sum_frames / duration, 100.0*sum_accs / sum_acc_frames)
        
        logger.removeHandler(fh)

        return avg_loss


    def read(self, filename):
        self.saver.restore(self.sess, filename)


    def write(self, filename) :
        return self.saver.save(self.sess, filename)


    def init_nnet(self):
        self.sess.run(self.init)


    def train(self, logfile, train_gen, learning_rate):
        '''Train one iteration'''

        fh = logging.FileHandler(logfile, mode = 'w')
        logger.addHandler(fh)

        sum_avg_loss = 0
        sum_accs = 0
        count_steps = 0

        sum_frames = 0
        sum_acc_frames = 0

        start_time = time.time()
        while(True):

            feed_dict = train_gen.get_batch(self.feats_holder, self.labels_holder)

            if feed_dict is None:   # no more data for training
                break

            feed_dict.update({self.learning_rate_holder: learning_rate})

            _, loss = self.sess.run([self.train_op, self.loss], feed_dict = feed_dict)

            sum_avg_loss += loss
            sum_frames += train_gen.get_batch_size()
            duration = time.time() - start_time
            count_steps += 1

            if count_steps % 1000 == 0 or count_steps == 1:
                acc = self.sess.run(self.eval_acc, feed_dict = feed_dict)
                sum_accs += acc
                sum_acc_frames += train_gen.get_batch_size()

                # Print status to stdout.
                logger.info("Step %5d: avg loss = %.6f on %d frames (%.2f sec passed, %.2f frames per sec), peek acc: %.2f%%", count_steps, sum_avg_loss / count_steps, sum_frames, duration, sum_frames / duration, 100.0*acc/train_gen.get_batch_size())

        train_gen.reset_batch()

        avg_loss = sum_avg_loss / count_steps

        logger.info("Complete: avg loss = %.6f on %d frames (%.2f sec passed, %.2f frames per sec), peek acc: %.2f%%", avg_loss, sum_frames, duration, sum_frames / duration, 100.0*sum_accs/sum_acc_frames)

        logger.removeHandler(fh)

        return avg_loss


    def patch_to_batches(self, feats):
        if len(feats) % self.batch_size == 0:
            return feats
        row2pad = self.batch_size - (len(feats) % self.batch_size)
        feat_dim = len(feats[0])
        feats_padded = np.vstack([feats, np.zeros([row2pad, feat_dim])])
        return feats_padded
        

    def predict(self, feats):
        feats_padded = self.patch_to_batches(feats)
        posteriors = []
        for i in range(len(feats_padded) // self.batch_size):
            batch_start = i*self.batch_size
            batch_end = (i+1)*self.batch_size
            feed_dict = {self.feats_holder: feats_padded[batch_start:batch_end, :]}
            batch_posteriors = self.sess.run(self.outputs, feed_dict=feed_dict)
            posteriors.append(batch_posteriors)

        posteriors = np.vstack(posteriors)
        return posteriors[0:len(feats),:]