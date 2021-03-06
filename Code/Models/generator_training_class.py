"""
# Headline Generation via Adversarial Training
## Project for Statistical Natural Language Processing (COMP0087)
## University College London

File: generator_training_class.py
Collaborators:
    - Daniel Stancl (ucabds7@ucl.ac.uk)
    - Guoliang HE (ucabggh@ucl.ac.uk)
    - Dorota Jagnesakova (ucabdj1@ucl.ac.uk)
    - Zakhar Borok (zcabzbo@ucl.ac.uk)

Description: This file contains the script for training class for the generator.
"""

# ----- Settings -----
import time
import gc
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import copy

exec(open('Code/Models/Attention_seq2seq.py').read())
# ----- Settings -----


class generator:
    """
    """

    def __init__(self, model, loss_function, optimiser, batch_size,
                 text_dictionary, embeddings, **kwargs):
        """
        Args:
            model -> seq2seq object

            loss_function -> loss function object to seq2seq

            optimiser -> optimiser object to seq2seq

            batch_size -> int

            text_dictionary ++++++

            embeddings -> Tensor pre-trained embedding weights

            kwargs -> dict args
        """
        # store some essential parameters and objects
        self.batch_size = batch_size
        self.text_dictionary = text_dictionary
        self.embeddings = embeddings

        ###---###
        self.grid = {'max_epochs': kwargs['max_epochs'],
                     'learning_rate': kwargs['learning_rate'],
                     'l2_reg': kwargs['l2_reg'],
                     'clip': kwargs['clip'],
                     # during training
                     'teacher_forcing_ratio': kwargs['teacher_forcing_ratio']
                     }
        OUTPUT_DIM = kwargs['OUTPUT_DIM']
        ENC_EMB_DIM = kwargs['ENC_EMB_DIM']
        #DEC_EMB_DIM = 1
        ENC_HID_DIM = kwargs['ENC_HID_DIM']
        DEC_HID_DIM = kwargs['DEC_HID_DIM']
        ENC_DROPOUT = kwargs['ENC_DROPOUT']
        DEC_DROPOUT = kwargs['DEC_DROPOUT']
        enc_num_layers = kwargs['enc_num_layers']
        dec_num_layers = kwargs['dec_num_layers']

        device = kwargs['device']

        self.model_name = kwargs['model_name']
        self.push_to_repo = kwargs['push_to_repo']

        self.device = device

        attn = _Attention(ENC_HID_DIM, DEC_HID_DIM)
        enc = _Encoder(ENC_EMB_DIM, ENC_HID_DIM, DEC_HID_DIM, rnn_num_layers=enc_num_layers,
                       dropout=ENC_DROPOUT, embeddings=embeddings, device=device)
        dec = _Decoder(output_dim=OUTPUT_DIM,  enc_hid_dim=ENC_HID_DIM, dec_hid_dim=DEC_HID_DIM, rnn_num_layers=dec_num_layers,
                       dropout=DEC_DROPOUT, attention=attn, embeddings=embeddings, device=device)
        self.model = model(enc, dec, device, embeddings,
                           text_dictionary).to(self.device)

        self.optimiser_ = optimiser
        self.loss_function = loss_function().to(self.device)

    def train(self, X_train, y_train, X_val, y_val,
              X_train_lengths, y_train_lengths, X_val_lengths, y_val_lengths):
        """
        Args:
            X_train, X_val -> Tensor.long(); list of token indices

            y_train, y_val -> Tensor.long(); list of token indices

            +++++
        """
        # measure the time of training
        start_time = time.time()
        # measure the time to print some output every 10 minutes
        time_1 = time.time()

        # generate batches
        # training data
        (input_train, input_train_lengths,
         target_train, target_train_lengths) = self._generate_batches(padded_input=X_train,
                                                                      input_lengths=X_train_lengths,
                                                                      padded_target=y_train,
                                                                      target_lengths=y_train_lengths)

        # validation data
        (input_val, input_val_lengths,
         target_val, target_val_lengths) = self._generate_batches(padded_input=X_val,
                                                                  input_lengths=X_val_lengths,
                                                                  padded_target=y_val,
                                                                  target_lengths=y_val_lengths)

        # Save number of batches of training and validation sets for a proper computation of losses
        self.n_batches = input_train.shape[0]
        self.n_batches_val = input_val.shape[0]

        # indices for reshuffling data before running each epoch
        input_arr = np.arange(input_train.shape[0])

        for epoch in range(self.start_epoch, self.grid['max_epochs']):
            # run the training
            self.model.train()
            epoch_loss = 0
            batch = 0

            # shuffle the data
            reshuffle = np.random.shuffle(input_arr)
            input_train, input_train_lengths = input_train[reshuffle].squeeze(
                0), input_train_lengths[reshuffle].squeeze(0)
            target_train, target_train_lengths = target_train[reshuffle].squeeze(
                0), target_train_lengths[reshuffle].squeeze(0)

            # Initialize optimiser for updating lr
            self.optimiser = self.optimiser_(self.model.parameters(), lr=(0.98**epoch) * self.grid['learning_rate'],
                                             weight_decay=self.grid['l2_reg'])

            for input, target, seq_length_input, seq_length_target in zip(input_train,
                                                                          target_train,
                                                                          input_train_lengths,
                                                                          target_train_lengths
                                                                          ):
                # counter
                batch += 1
                # zero gradient
                self.optimiser.zero_grad()
                # FORWARD PASS
                # Prepare RNN-edible input - i.e. pack padded sequence
                # trim input, target
                input = torch.from_numpy(
                    input[:seq_length_input.max()]
                ).long()
                target = torch.from_numpy(
                    target[:seq_length_target.max()]
                ).long().to(self.device)

                output = self.model(seq2seq_input=input, input_lengths=seq_length_input,
                                    target=target, teacher_forcing_ratio=self.grid['teacher_forcing_ratio']
                                    )

                output = F.log_softmax(output, dim=2)
                del input
                torch.cuda.empty_cache()

                # Pack output and target padded sequence
                # Determine a length of output sequence based on the first occurrence of <eos>
                seq_length_output = (output.argmax(
                    2) == self.text_dictionary.word2index['eos']).int().argmax(0).cpu().numpy()
                seq_length_output += 1

                # determine seq_length for computation of loss function based on max(seq_lenth_target, seq_length_output)
                seq_length_loss = np.array(
                    (seq_length_output, seq_length_target)
                ).max(0)

                output = nn.utils.rnn.pack_padded_sequence(output,
                                                           lengths=seq_length_loss,
                                                           batch_first=False,
                                                           enforce_sorted=False).to(self.device)

                target = nn.utils.rnn.pack_padded_sequence(target,
                                                           lengths=seq_length_loss,
                                                           batch_first=False,
                                                           enforce_sorted=False).to(self.device)

                # Compute loss
                loss = self.loss_function(output[0], target[0])

                # BACKWARD PASS
                # Make update step w.r.t. clipping gradient
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    self.model.parameters(), self.grid['clip'])
                self.optimiser.step()

                epoch_loss += loss.item()
                # clearing
                del output, target
                torch.cuda.empty_cache()

                # print some outputs if desired (i.e., each 10 minuts)
                time_2 = time.time()
                if (time_2 - time_1) > 60:
                    print("Epoch {:.0f} - Intermediate loss {:.3f} after {:.2f} % of training examples.".format(epoch+1,
                                                                                                                epoch_loss / batch,
                                                                                                                100 * batch / self.n_batches))
                    print('Total time {:.1f} s.'.format(
                        time.time() - start_time))
                    np.savetxt(
                        'Results/{}__train_time.txt'.format(self.model_name), X=[time.time() - start_time])
                    torch.save(self.model.state_dict(
                    ), "../data/Results/Intermediate_{}.pth".format(self.model_name))
                    time_1 = time.time()

            # Save training loss and validation loss
            self.train_losses.append(epoch_loss/self.n_batches)

            self.val_losses.append(
                self._evaluate(input_val, input_val_lengths,
                               target_val, target_val_lengths)/self.n_batches_val
            )

            # Store the best model if validation loss improved
            if self.val_losses[epoch] < self.best_val_loss:
                self.best_val_loss = self.val_losses[epoch]
                # And save the model state
                self.m = copy.deepcopy(self.model)
                self.save()

            # Print the progress
            print(f'Epoch: {epoch+1}:')
            print(f'Train Loss: {self.train_losses[epoch]:.3f}')
            print(f'Validation Loss: {self.val_losses[epoch]:.3f}')

            # Save training results and push everything to git
            training_GPU_time = np.array(
                [str(torch.cuda.get_device_name()).split(
                    '-')[0], time.time() - start_time]
            )
            np.savetxt(
                'Results/{}__train_loss.txt'.format(self.model_name), X=self.train_losses)
            np.savetxt(
                'Results/{}__validation_loss.txt'.format(self.model_name), X=self.val_losses)
            np.savetxt('Results/{}__training_time.txt'.format(self.model_name),
                       X=training_GPU_time, fmt='%s')
            self.push_to_repo()

            # End training if the model has already converged
            if epoch >= 2:
                if self.train_losses[epoch] > self.train_losses[epoch-2]:
                    statement = "The model has converged after {:.0f} epochs.".format(
                        epoch+1)
                    return statement

    def _evaluate(self, input_val, input_val_lengths, target_val, target_val_lengths):
        """
        Args:++++++++++
            input_val

            input_val_lengths

            target_val

            target_val_lengths
        """
        self.model.eval()
        val_loss = 0
        for input, target, seq_length_input, seq_length_target in zip(input_val,
                                                                      target_val,
                                                                      input_val_lengths,
                                                                      target_val_lengths
                                                                      ):
            # FORWARD PASS
            # Prepare RNN-edible input - i.e. pack padded sequence
            # trim input, target
            input = torch.from_numpy(
                input[:seq_length_input.max()]
            ).long()
            target = torch.from_numpy(
                target[:seq_length_target.max()]
            ).long().to(self.device)

            output = self.model(seq2seq_input=input, input_lengths=seq_length_input,
                                target=target, teacher_forcing_ratio=self.grid['teacher_forcing_ratio']
                                )

            output = F.log_softmax(output, dim=2)
            del input
            torch.cuda.empty_cache()

            # Pack output and target padded sequence
            # Determine a length of output sequence based on the first occurrence of <eos>
            seq_length_output = (output.argmax(
                2) == self.text_dictionary.word2index['eos']).int().argmax(0).cpu().numpy()
            seq_length_output += 1

            # determine seq_length for computation of loss function based on max(seq_lenth_target, seq_length_output)
            seq_length_loss = np.array(
                (seq_length_output, seq_length_target)
            ).max(0)

            output = nn.utils.rnn.pack_padded_sequence(output,
                                                       lengths=seq_length_loss,
                                                       batch_first=False,
                                                       enforce_sorted=False).to(self.device)

            target = nn.utils.rnn.pack_padded_sequence(target,
                                                       lengths=seq_length_loss,
                                                       batch_first=False,
                                                       enforce_sorted=False).to(self.device)

            # Compute loss
            val_loss += self.loss_function(output[0], target[0]).item()

        return val_loss

    def generate_summaries(self, input_val, input_val_lengths, target_val, target_val_lengths):
        """
        Args:++++++++++
            input_val

            input_val_lengths

            target_val

            target_val_lengths
        """
        self.model.eval()

        (input_val, input_val_lengths,
         target_val, target_val_lengths) = self._generate_batches(padded_input=input_val,
                                                                  input_lengths=input_val_lengths,
                                                                  padded_target=target_val,
                                                                  target_lengths=target_val_lengths)
        OUTPUT = []
        for input, target, seq_length_input, seq_length_target in zip(input_val,
                                                                      target_val,
                                                                      input_val_lengths,
                                                                      target_val_lengths
                                                                      ):
            # FORWARD PASS
            # Prepare RNN-edible input - i.e. pack padded sequence
            # trim input, target
            input = torch.from_numpy(
                input[:seq_length_input.max()]
            ).long()
            target = torch.from_numpy(
                target[:seq_length_target.max()]
            ).long().to(self.device)

            output = self.model(seq2seq_input=input, input_lengths=seq_length_input,
                                target=target, teacher_forcing_ratio=0)
            del input, target
            torch.cuda.empty_cache()

            OUTPUT.append(
                output.argmax(dim=2).cpu().numpy()
            )

        return np.array(OUTPUT)

    def _generate_batches(self, padded_input, input_lengths, padded_target, target_lengths):
        """
        Args:++++++++++
            padded_input

            input_lengths

            padded_target

            target_lengths
        """
        # determine a number of batches
        n_batches = padded_input.shape[1] // self.batch_size
        self.n_batches = n_batches

        # Generate input and target batches
        # dimension => [total_batchs, seq_length, batch_size, embed_dim], for target embed_dim is irrelevant
        # seq_length is variable throughout the batches
        input_batches = np.array(
            np.split(
                padded_input[:, :(n_batches * self.batch_size)], n_batches, axis=1)
        )
        target_batches = np.array(
            np.split(
                padded_target[:, :(n_batches * self.batch_size)], n_batches, axis=1)
        )
        # Split input and target lenghts into batches as well
        input_lengths = np.array(
            np.split(
                input_lengths[:(n_batches * self.batch_size)], n_batches, axis=0)
        )
        target_lengths = np.array(
            np.split(
                target_lengths[:(n_batches * self.batch_size)], n_batches, axis=0)
        )

        """
        # trim sequences in individual batches
        for batch in range(n_batches):
            input_batches[batch] = input_batches[batch, input_lengths[batch].max():, :, :]
            target_batches[batch] = target_batches[batch, target_lengths[batch].max():, :]
        """
        # return prepared data
        return (input_batches, input_lengths,
                target_batches, target_lengths)

    def save(self):
        """
        save the model to default path
        """
        torch.save(self.m.state_dict(),
                   "../data/Results/{}.pth".format(self.model_name))

    def load(self, demo = False):
        """
        load the model to default path
        """
        if demo == False:
            try:
                self.model.load_state_dict(torch.load(
                    "../data/Results/{}.pth".format(self.model_name)))
                self.train_losses = np.loadtxt(
                    'Results/{}__train_loss.txt'.format(self.model_name)).tolist()
                self.val_losses = np.loadtxt(
                    'Results/{}__validation_loss.txt'.format(self.model_name)).tolist()
                # proper formatting
                try:
                    # utilized when 2 or more epochs have already been done
                    self.train_losses = sum([self.train_losses], [])
                    self.val_losses = sum([self.val_losses], [])
                except:
                    # applied when a single has been completed
                    self.train_losses = [self.train_losses]
                    self.val_losses = [self.val_losses]

                self.best_val_loss = min(self.val_losses)
                self.start_epoch = len(self.train_losses)
            except:
                self.start_epoch = 0
                self.train_losses, self.val_losses = [], []
                self.best_val_loss = float('inf')
        
        elif demo == True:
            self.model.load_state_dict(torch.load(
                    "DemoFiles/Models/{}.pth".format(self.model_name)))
